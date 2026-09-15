"""Where every in-RAM bucket in the 256MB budget reports its own size.

SA-2256. The budget table in SA-2254 lists what the control plane holds in
memory: the projector's three per-run dicts, the run-scoped materialize and
OData caches, the messaging caches (M2-M4) and the realtime queues. Until this
existed, none of them were observable: the only way to know a cache had grown
was for the pod to die.

Pull, not push
--------------
Buckets are read AT SCRAPE TIME by a Prometheus collector, not pushed on a
timer. A pushed gauge is only as fresh as its last push, so a cache that grows
between pushes is invisible for exactly as long as it takes to matter. Pulling
also means a bucket costs nothing when nobody scrapes.

Weak membership
---------------
As with :mod:`run_scoped_cache_registry`, members are held weakly — an
observability registry that pinned every cache it observed would be a leak
introduced by the leak detector.

Contract
--------
A member exposes ``budget_snapshot() -> dict[str, int | float]``. Keys are
free-form per bucket (``entries``, ``maxsize``, ``hits``, ...); the collector
turns each into ``wcp_cache_<key>{bucket="<name>"}``. Report counts, not
estimated bytes: a byte estimate here would be a guess dressed as a number, and
the numbers that decide the budget come from P0-3's measurement instead.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class BudgetReporting(Protocol):
    """Anything holding memory that the 256MB budget must account for."""

    def budget_snapshot(self) -> dict:  # pragma: no cover - protocol
        ...


class CacheBudgetRegistry:
    """Every in-RAM bucket that wants to appear in the memory budget."""

    __slots__ = ("_members", "_lock")

    def __init__(self) -> None:
        self._members: list[tuple[weakref.ReferenceType, str]] = []
        self._lock = threading.Lock()

    def register(self, bucket: BudgetReporting, name: str) -> None:
        """Join the budget export under ``name``. Re-registering is a no-op."""
        if not hasattr(bucket, "budget_snapshot"):
            raise TypeError(
                f"{type(bucket).__name__} has no budget_snapshot() — it cannot "
                "join the cache budget registry"
            )
        if not name:
            raise ValueError("a budget bucket needs a stable name")
        with self._lock:
            self._prune_locked()
            for ref, existing in self._members:
                if ref() is bucket and existing == name:
                    return
            self._members.append((weakref.ref(bucket), name))
        logger.debug("cache budget bucket registered: %s", name)

    def unregister(self, bucket: BudgetReporting) -> bool:
        with self._lock:
            before = len(self._members)
            self._members = [
                (ref, name) for ref, name in self._members
                if ref() is not None and ref() is not bucket
            ]
            return len(self._members) != before

    def snapshot(self) -> dict[str, dict]:
        """Read every live bucket right now.

        A bucket that raises is skipped with a warning rather than breaking the
        scrape — observability must never be the thing that takes the pod down.
        When two buckets share a name their snapshots are merged by summing, so
        N replicas of the same component report one number instead of silently
        overwriting each other.
        """
        with self._lock:
            self._prune_locked()
            members = list(self._members)
        out: dict[str, dict] = {}
        for ref, name in members:
            bucket = ref()
            if bucket is None:
                continue
            try:
                values = bucket.budget_snapshot() or {}
            except Exception:
                logger.warning(
                    "cache budget bucket %s failed to report — skipping it in "
                    "this scrape", name, exc_info=True,
                )
                continue
            target = out.setdefault(name, {})
            for key, value in values.items():
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                target[key] = target.get(key, 0) + value
        return out

    def names(self) -> list[str]:
        with self._lock:
            self._prune_locked()
            return sorted({name for _, name in self._members})

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._members)

    def _prune_locked(self) -> None:
        self._members = [(r, n) for r, n in self._members if r() is not None]


_DEFAULT_REGISTRY = CacheBudgetRegistry()


def default_cache_budget_registry() -> CacheBudgetRegistry:
    """Return the process-wide budget registry."""
    return _DEFAULT_REGISTRY
