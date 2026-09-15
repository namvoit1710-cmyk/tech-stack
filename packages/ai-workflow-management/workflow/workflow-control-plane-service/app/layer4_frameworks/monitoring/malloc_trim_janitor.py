"""Actually call ``malloc_trim()`` on a timer.

Why this file has to exist
--------------------------
``resource_collector.malloc_trim`` shipped in #1962 and **nothing called it**.
Its own docstring said why it was left unwired — "Measure before you schedule
this" — and that measurement now exists.

Measured on tenant-1, 2026-08-21, over 7.5 h of Prometheus at 25-minute rows:

    01:38  RSS 1141.4 MiB  arena 976.6  in_use 53.6
    04:33  RSS 1147.6 MiB  arena 976.6  in_use 41.0
    08:43  RSS 1155.7 MiB  arena 976.6  in_use 42.5

    RSS drops > 8 MiB in 7.5 h:  1  ->  the process RESTARTING
    arena drops > 8 MiB:         1  ->  the same event

The arena held the *identical* value for seven hours while ``in_use`` sat at
42 MiB, the pod served 5 orchestration hops and 2 node executions, and every
named cache read 0 entries. A freshly booted control plane idles at 131 MiB;
that one idled at 1156 MiB. **The only mechanism that has ever returned memory
on this pod is the process dying.**

Read inside the container the same day, ``/proc/8/smaps``::

    [heap]  Size 904 944 kB   Rss 904 944 kB   Private_Dirty 904 944 kB

100% of the heap resident and dirty, and ``in_use`` says 42 MiB of it holds
anything.

What decides that a trim is worth it
------------------------------------
Not hope — ``read_malloc_info``'s own rule, which this module simply applies:
``free / free_chunks`` above one page means the retention is page-releasable,
below a page it is sub-page fragmentation that no trim can return. Measured:
934 MiB free in 1 459 chunks = **655 KiB mean, 164x the 4 KiB page**; and after
a single 54.55 MiB ``POST /runs``, 707 MiB free in 671 chunks = **1 079 KiB,
263x**. Both are far over the bar, so the gate is not decorative — it is the
thing that stops this janitor from burning loop time on a pod whose retention is
genuinely unrecoverable.

The cost, and why the interval alone is not enough
-------------------------------------------------
``malloc_trim`` is SYNCHRONOUS and it blocks. The benchmark in
``read_malloc_info`` is **0.3 ms** at 2 KiB free chunks and **54.6 ms** at 32 KiB
*with the chunk count held constant* — a 180x spread, because cost tracks
releasable PAGES. This pod's chunks are 655-1079 KiB, twenty times past the
slower benchmark point, so the honest position is that **the cost here has never
been measured** and the first call after a spike is the most expensive one there
will ever be.

On a control plane pinned at ``instances: 1`` that time is event-loop time, and
event-loop time is the whole tenant. So a fixed interval is not a safety
property: if a trim turns out to cost 500 ms, a 300 s timer inflicts it forever.
This janitor therefore **backs off on its own cost** — a trim over
``max_duration_ms`` doubles the effective interval, up to 8x, and says so at
WARNING. Recovery is automatic once a trim comes in under budget.

Not offloaded to a thread, deliberately. ``malloc_trim`` takes each arena's lock
in turn, and a CPython event loop allocates on essentially every bytecode; the
loop would block on the allocator regardless. Running it in an executor would
move where the stall is reported without moving the stall, which is worse than
not doing it — the same class of mistake as a metric that observes the wrong
path.

What this does NOT fix
----------------------
The retention is *created* by the ``POST /runs`` body ingest — measured
2026-08-21 on a fresh process, one 54.55 MiB body grew the arena by 968 MiB and
froze the event loop for 4.65 s, while six concurrent runs reading ~61.7 MiB
total added ZERO to the high-water. Trimming gives the pages back afterwards; it
does not stop the pod reaching the high-water in the first place, and it cannot
help at all during the spike itself. Capping the ingest is a separate decision
that has been made deliberately and is NOT taken here: runs are not to be
limited by input size.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from app.layer4_frameworks.monitoring.resource_collector import (
    malloc_trim,
    read_malloc_info,
)

_logger = logging.getLogger(__name__)

#: Trims attempted, labelled by outcome. Bounded at six values, and the label
#: key never varies: ``PrometheusMonitor._get_counter`` freezes a metric's label
#: set on first observation and drops later mismatched increments SILENTLY.
TRIM_COUNTER = "malloc_trim_total"

#: Bytes of RSS returned to the OS, summed. Untagged, always — see
#: above. RSS and not arena: see ``_trim_once``.
RELEASED_COUNTER = "malloc_trim_released_bytes_total"

#: Trim wall time. In SECONDS, not milliseconds, because
#: ``PrometheusMonitor._get_histogram`` hard-codes 0.001-5.0 request-latency
#: buckets with no way to pass others. A trim in seconds lands inside them; the
#: same value in milliseconds would pile into ``+Inf`` and measure nothing,
#: which is the defect ``eventmesh_flush_*`` already carries.
DURATION_HISTOGRAM = "malloc_trim_duration_seconds"

#: glibc releases whole free PAGES. Sub-page fragments are unreclaimable no
#: matter how many bytes they total, so this is the threshold that decides
#: whether calling at all can achieve anything.
_PAGE_BYTES = 4096

_OUTCOME_RELEASED = "released"
_OUTCOME_NOTHING = "nothing"
_OUTCOME_SMALL = "skipped_small"
_OUTCOME_FRAGMENTED = "skipped_fragmented"
_OUTCOME_UNAVAILABLE = "unavailable"
_OUTCOME_ERROR = "error"


class MallocTrimJanitor:
    """Returns page-releasable free arena to the OS on a timer."""

    __slots__ = ("_monitor", "_base_interval", "_interval", "_min_free",
                 "_max_duration_ms", "_pad", "_stop", "_trims", "_released",
                 "_skipped", "_errors", "_last_trim_at", "_last_result",
                 "_unavailable", "_backoff")

    #: Cap on the self-imposed back-off. Beyond 8x a 300 s timer is 40 minutes,
    #: which is far enough from "useful" that the right response is an operator
    #: reading the WARNING, not a janitor that keeps halving itself into never.
    _MAX_BACKOFF = 8

    def __init__(
        self,
        monitor: Any,
        *,
        interval_s: float = 300.0,
        min_free_bytes: int = 64 * 1024 * 1024,
        max_duration_ms: float = 100.0,
        pad_bytes: int = 0,
    ) -> None:
        self._monitor = monitor
        # A zero or negative interval would spin the loop. Disabling belongs in
        # config (ENABLED), not in a magic 0 — the same rule CacheExpiryJanitor
        # follows, for the same reason.
        self._base_interval = max(1.0, float(interval_s))
        self._interval = self._base_interval
        self._min_free = max(0, int(min_free_bytes))
        self._max_duration_ms = max(1.0, float(max_duration_ms))
        self._pad = max(0, int(pad_bytes))
        self._stop = asyncio.Event()
        self._trims = 0
        self._released = 0
        self._skipped = 0
        self._errors = 0
        self._backoff = 1
        self._last_trim_at = 0.0
        self._last_result: Optional[dict] = None
        self._unavailable = False

    async def run_forever(self) -> None:
        """Trim until :meth:`stop` is called. Start via ``create_task``."""
        _logger.info(
            "malloc_trim janitor started (interval=%.0fs, min_free=%d MiB, "
            "max_duration=%.0fms, pad=%d)",
            self._base_interval, self._min_free // (1024 * 1024),
            self._max_duration_ms, self._pad,
        )
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
                break  # stop was set
            except asyncio.TimeoutError:
                pass
            self.trim_once()
            if self._unavailable:
                # Not glibc. Nothing will ever change that within a process, and
                # a timer that wakes forever to re-learn it is pure loop time.
                _logger.info(
                    "malloc_trim janitor stopping: this platform has no "
                    "malloc_trim, so there is nothing to schedule.",
                )
                break
        _logger.info(
            "malloc_trim janitor stopped after %d trims, %d MiB released, "
            "%d skipped, %d errors",
            self._trims, self._released // (1024 * 1024), self._skipped,
            self._errors,
        )

    def stop(self) -> None:
        self._stop.set()

    def trim_once(self) -> dict:
        """One gated trim. Synchronous and safe to call from a test directly.

        Never raises. A janitor that dies on its first bad trim silently stops
        bounding the pod's RSS, and the symptom would surface days later as
        memory that only grows — exactly the failure this exists to end.
        """
        try:
            return self._trim_once()
        except Exception:  # noqa: BLE001 - the loop must outlive a bad trim
            self._errors += 1
            _logger.warning("malloc_trim sweep failed", exc_info=True)
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_ERROR})
            return {"outcome": _OUTCOME_ERROR}

    def _trim_once(self) -> dict:
        info = read_malloc_info()
        if not info:
            self._unavailable = True
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_UNAVAILABLE})
            return {"outcome": _OUTCOME_UNAVAILABLE}

        free = int(info.get("free") or 0)
        chunks = int(info.get("free_chunks") or 0)

        if free < self._min_free:
            self._skipped += 1
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_SMALL})
            return {"outcome": _OUTCOME_SMALL, "free": free}

        # ``read_malloc_info``'s own rule, applied rather than restated: a mean
        # free chunk above one page is reclaimable, below a page it is not, and
        # no quantity of sub-page fragments changes that.
        mean_chunk = (free / chunks) if chunks > 0 else 0.0
        if mean_chunk < _PAGE_BYTES:
            self._skipped += 1
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_FRAGMENTED})
            _logger.info(
                "malloc_trim skipped: %d MiB free but mean chunk %.0f B is "
                "under the %d B page, so none of it is page-releasable",
                free // (1024 * 1024), mean_chunk, _PAGE_BYTES,
            )
            return {"outcome": _OUTCOME_FRAGMENTED, "free": free,
                    "mean_chunk": mean_chunk}

        result = malloc_trim(self._pad)
        if result is None:
            self._unavailable = True
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_UNAVAILABLE})
            return {"outcome": _OUTCOME_UNAVAILABLE}

        self._trims += 1
        self._last_trim_at = time.time()
        self._last_result = result

        # RSS, not arena. Measured live 2026-08-21 with this janitor running:
        # a trim took RSS from 927.7 MiB to 169.4 MiB while ``arena`` did not
        # move one byte, and this counted it as ``nothing``. glibc releases
        # INTERIOR free pages with ``MADV_DONTNEED`` — the address space stays
        # mapped, the residency goes — and ``mallinfo2.arena`` measures the
        # address space. Arena only moves when the heap TOP shrinks, which is a
        # real event but a rarer one, so it is the fallback rather than the
        # signal.
        released = result.get("rss_released_bytes")
        if released is None:
            released = result.get("arena_released_bytes")
        released = int(released or 0)
        duration_ms = float(result.get("duration_ms") or 0.0)
        self._observe(DURATION_HISTOGRAM, duration_ms / 1000.0)

        if released > 0:
            self._released += released
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_RELEASED})
            self._increment(RELEASED_COUNTER, None, released)
            _logger.info(
                "malloc_trim released %d MiB of RSS in %.1f ms "
                "(rss %d -> %d MiB, arena %d -> %d MiB, mean free chunk "
                "%.0f KiB)",
                released // (1024 * 1024), duration_ms,
                int(result.get("rss_before") or 0) // (1024 * 1024),
                int(result.get("rss_after") or 0) // (1024 * 1024),
                int(result.get("arena_before") or 0) // (1024 * 1024),
                int(result.get("arena_after") or 0) // (1024 * 1024),
                mean_chunk / 1024,
            )
        else:
            # ``returned=1`` with 0 bytes released is a real state: glibc says
            # it gave pages back and neither RSS nor the arena moved. Distinct
            # from "there was nothing to release", which is why malloc_trim
            # reports all three and this does not collapse them. Measured on
            # tenant-1 this is the steady state once the retention is gone: the
            # trim costs ~0.5 ms and returns nothing, which is correct.
            self._increment(TRIM_COUNTER, {"outcome": _OUTCOME_NOTHING})
            _logger.info(
                "malloc_trim released nothing in %.1f ms (returned=%s, "
                "%d MiB free, mean chunk %.0f KiB)",
                duration_ms, result.get("returned"), free // (1024 * 1024),
                mean_chunk / 1024,
            )

        self._adjust_backoff(duration_ms)
        return {"outcome": _OUTCOME_RELEASED if released > 0
                else _OUTCOME_NOTHING, **result}

    def _adjust_backoff(self, duration_ms: float) -> None:
        """Slow down when a trim costs more than it is allowed to.

        The interval is not a safety property on its own: this call has never
        been measured on a pod with chunks this large, and if it turns out to
        cost half a second, a fixed timer inflicts that on the whole tenant
        forever. Doubling on every over-budget trim bounds the damage to
        ``max_duration_ms`` per ``interval * backoff`` without needing anyone to
        have guessed the cost right in advance.
        """
        if duration_ms > self._max_duration_ms:
            if self._backoff < self._MAX_BACKOFF:
                self._backoff = min(self._MAX_BACKOFF, self._backoff * 2)
            self._interval = self._base_interval * self._backoff
            _logger.warning(
                "malloc_trim took %.1f ms, over the %.0f ms budget — this is "
                "event-loop time and the plane runs at instances:1. Backing "
                "off to %.0fs (%dx). Watch event_loop_lag_seconds.",
                duration_ms, self._max_duration_ms, self._interval,
                self._backoff,
            )
        elif self._backoff != 1:
            self._backoff = 1
            self._interval = self._base_interval
            _logger.info(
                "malloc_trim back under budget (%.1f ms); interval restored "
                "to %.0fs", duration_ms, self._interval,
            )

    # -- the monitor may be absent or a test double; never break the loop --

    def _increment(
        self, metric: str, tags: dict | None, amount: int = 1,
    ) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.increment(metric, tags, amount)
        except Exception:  # noqa: BLE001 - metrics must never break the trim
            pass

    def _observe(self, metric: str, value: float) -> None:
        if self._monitor is None:
            return
        try:
            self._monitor.histogram(metric, value)
        except Exception:  # noqa: BLE001 - metrics must never break the trim
            pass

    def snapshot(self) -> dict:
        """Janitor state. For tests and in-pod probes."""
        return {
            "trims": self._trims,
            "released_bytes": self._released,
            "skipped": self._skipped,
            "errors": self._errors,
            "last_trim_at": self._last_trim_at,
            "last_result": self._last_result,
            "interval_s": self._interval,
            "base_interval_s": self._base_interval,
            "backoff": self._backoff,
            "min_free_bytes": self._min_free,
            "max_duration_ms": self._max_duration_ms,
            "pad_bytes": self._pad,
            "unavailable": self._unavailable,
        }


__all__ = [
    "MallocTrimJanitor",
    "TRIM_COUNTER",
    "RELEASED_COUNTER",
    "DURATION_HISTOGRAM",
]
