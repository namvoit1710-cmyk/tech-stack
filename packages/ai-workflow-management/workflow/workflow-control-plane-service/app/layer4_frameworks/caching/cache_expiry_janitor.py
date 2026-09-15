"""Actually call ``purge_expired()`` on a timer (SA-2368).

Why this file has to exist
--------------------------
``BoundedTTLCache`` has always been able to drop expired entries, and
``purge_expired()`` has always been public. Before this, **nothing in the service
called it** — the only expiry that ever ran was the lazy one inside ``get()``,
which visits exactly the key being read.

For a cache keyed by ``run_id`` that is the difference between a bound and no
bound. Once a run ends nobody reads its key again, so its entry is never visited,
and the TTL bounds only *staleness*: the value stays resident until the LRU cap
pushes it out. ``bounded_ttl_cache``'s own docstring predicted this ("a lazily
expiring TTL never visits the ones nobody reads again") and then the service
shipped without the sweep that would have answered it.

Measured on tenant-1, 2026-08-12, 13.5h uptime: ``run_credentials`` held 74
entries with ``evictions=0`` and ``expirations=1`` — so every credential the
control plane had decrypted since boot was still in memory, a residency of about
**3.9 days against a 300s TTL**. The entries are plaintext ``{type, config}``
straight from the vault.

What it does and does not fix
-----------------------------
This bounds residency to ``TTL + interval`` for **every** named cache, which is
the general fix and needs no per-bucket knowledge. It does NOT make a credential
disappear the moment its run finishes — that is ``RunScopedTTLCache.evict_run``,
which fires on the terminal transition. The two are deliberately independent: a
run that never reaches a terminal state (per SA-1725 roughly a third of loops do
not) gets no eviction event at all, and this timer is what still collects it.

Cost
----
One wakeup per ``interval_s`` and one pass over each cache's key list. The whole
budget is a few thousand entries across seven buckets, so a sweep is microseconds
of loop time; at the 60s default it is not measurable against a service whose
ceiling is event-loop time. The sweep takes each cache's own lock briefly and
never awaits while holding one.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.layer4_frameworks.caching.bounded_ttl_cache import (
    purge_all_expired,
    sweepable_names,
)

_logger = logging.getLogger(__name__)

#: Entries dropped, labelled by bucket. Always carries the same label key —
#: a metric whose label set varies between calls has its later samples dropped
#: silently by prometheus_client.
PURGED_COUNTER = "cache_expiry_purged_total"

#: Sweeps completed. Untagged, and it exists so a permanent zero on
#: ``PURGED_COUNTER`` can be read correctly: without it, "nothing was expired"
#: and "the janitor is dead" look identical on a dashboard.
SWEEPS_COUNTER = "cache_expiry_sweeps_total"


class CacheExpiryJanitor:
    """Sweeps expired entries out of every named ``BoundedTTLCache``."""

    __slots__ = ("_monitor", "_interval", "_stop", "_sweeps", "_purged",
                 "_last_sweep_at", "_errors")

    def __init__(self, monitor: Any, *, interval_s: float = 60.0) -> None:
        self._monitor = monitor
        # A zero or negative interval would spin the loop; the floor is a
        # second. Disabling belongs in config (ENABLED), not in a magic 0.
        self._interval = max(1.0, float(interval_s))
        self._stop = asyncio.Event()
        self._sweeps = 0
        self._purged = 0
        self._errors = 0
        self._last_sweep_at = 0.0

    async def run_forever(self) -> None:
        """Sweep until :meth:`stop` is called. Start via ``create_task``."""
        _logger.info(
            "Cache expiry janitor started (interval=%.0fs, buckets=%s)",
            self._interval, ",".join(sweepable_names()) or "none",
        )
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                break  # stop was set
            except asyncio.TimeoutError:
                pass
            self.sweep_once()
        _logger.info(
            "Cache expiry janitor stopped after %d sweeps, %d entries purged",
            self._sweeps, self._purged,
        )

    def stop(self) -> None:
        self._stop.set()

    def sweep_once(self) -> dict[str, int]:
        """One sweep. Synchronous and safe to call from a test directly.

        Never raises: a janitor that dies on its first bad sweep silently stops
        bounding every cache in the service, and the symptom would appear days
        later as memory that only grows.
        """
        try:
            dropped = purge_all_expired()
        except Exception:  # noqa: BLE001 - the loop must outlive a bad sweep
            self._errors += 1
            _logger.warning("cache expiry sweep failed", exc_info=True)
            return {}
        self._sweeps += 1
        self._last_sweep_at = time.time()
        self._increment(SWEEPS_COUNTER, None)
        for bucket, count in dropped.items():
            self._purged += count
            self._increment(PURGED_COUNTER, {"bucket": bucket}, count)
        if dropped:
            _logger.debug(
                "cache expiry sweep dropped %s",
                ", ".join(f"{k}={v}" for k, v in sorted(dropped.items())),
            )
        return dropped

    # -- the monitor may be absent or a test double; never break the loop --

    def _increment(
        self, metric: str, tags: dict | None, amount: int = 1,
    ) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.increment(metric, tags, amount)
        except Exception:  # noqa: BLE001 - metrics must never break the sweep
            pass

    def snapshot(self) -> dict:
        """Janitor state. For tests and in-pod probes."""
        return {
            "sweeps": self._sweeps,
            "purged": self._purged,
            "errors": self._errors,
            "last_sweep_at": self._last_sweep_at,
            "interval_s": self._interval,
            "buckets": sweepable_names(),
        }


__all__ = ["CacheExpiryJanitor", "PURGED_COUNTER", "SWEEPS_COUNTER"]
