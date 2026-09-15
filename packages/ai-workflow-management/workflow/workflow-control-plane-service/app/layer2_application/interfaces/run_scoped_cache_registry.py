"""An explicit join-point for caches that must die when a run does.

Why a registry and not another scan
-----------------------------------
Run-scoped cleanup used to be discovered by duck-typing::

    strategies = getattr(self._variable_resolver, "_strategies", None)
    for strategy in strategies:
        drop = getattr(strategy, "drop_run_cache", None)
        if callable(drop):
            drop(run_id)

That reaches exactly one place — the variable resolver's private strategy
list — and silently reaches nothing else. ``CachedProjectorRunContextViewStore``
holds three per-run dicts and is not a resolver strategy, so the scan never saw
it and every run ever projected stayed resident for the pod's lifetime
(SA-2258). The scan did not fail loudly; it simply had nothing to say, which is
the worst property a cleanup mechanism can have.

So: components ANNOUNCE themselves. A cache that is keyed by ``run_id``
registers once and is guaranteed to be told when that run reaches a terminal
state. Adding a fourth run-scoped cache later is one ``register()`` call, not a
hunt for whichever scan happens to reach it.

Invariants
----------
* **The registry is not itself a leak.** Members are held by weak reference, so
  registering a cache never keeps it alive, and dead entries are pruned on
  every pass. A registry that pinned every store ever built would be the same
  bug one level up.
* **One bad member cannot block the rest.** ``release_run`` isolates each
  member: an exception is logged and the sweep continues. Cleanup that stops
  halfway is how a partial leak becomes a total one.
* **Idempotent.** Releasing a run that was never cached, or releasing twice, is
  a no-op — terminal transitions can fire more than once.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class RunScopedCache(Protocol):
    """Anything holding per-``run_id`` state that a terminal run should free."""

    def evict_run(self, run_id: str) -> None:  # pragma: no cover - protocol
        ...


class RunScopedCacheRegistry:
    """Components join here; a terminal run sweeps every member."""

    __slots__ = ("_members", "_lock")

    def __init__(self) -> None:
        # (weakref to the member, display name) — weak so membership never
        # extends a member's life.
        self._members: list[tuple[weakref.ReferenceType, str]] = []
        self._lock = threading.Lock()

    def register(self, cache: RunScopedCache, name: str = "") -> None:
        """Join the sweep. Registering the same object twice is a no-op."""
        if not hasattr(cache, "evict_run"):
            raise TypeError(
                f"{type(cache).__name__} has no evict_run(run_id) — it cannot "
                "join the run-scoped cache registry"
            )
        label = name or type(cache).__name__
        with self._lock:
            self._prune_locked()
            for ref, _ in self._members:
                if ref() is cache:
                    return
            self._members.append((weakref.ref(cache), label))
        logger.debug("run-scoped cache registered: %s", label)

    def unregister(self, cache: RunScopedCache) -> bool:
        """Leave the sweep. Returns True if the member was present."""
        with self._lock:
            before = len(self._members)
            self._members = [
                (ref, name) for ref, name in self._members
                if ref() is not None and ref() is not cache
            ]
            return len(self._members) != before

    def release_run(self, run_id: str) -> int:
        """Tell every live member to drop ``run_id``. Returns members swept.

        Never raises: a member that fails is logged and skipped, because a
        cleanup pass that aborts partway through leaves the remaining caches
        holding the run forever.
        """
        if not run_id:
            return 0
        with self._lock:
            self._prune_locked()
            snapshot = list(self._members)
        swept = 0
        for ref, name in snapshot:
            member = ref()
            if member is None:
                continue
            try:
                member.evict_run(run_id)
                swept += 1
            except Exception:
                logger.warning(
                    "run-scoped cache %s failed to evict run %s — continuing "
                    "so the remaining caches still release it",
                    name, run_id, exc_info=True,
                )
        return swept

    def members(self) -> list[str]:
        """Names of the live members, for diagnostics and the SA-2256 export."""
        with self._lock:
            self._prune_locked()
            return [name for _, name in self._members]

    def __len__(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._members)

    def _prune_locked(self) -> None:
        self._members = [(r, n) for r, n in self._members if r() is not None]


# The process-wide registry. Components default to this so joining costs one
# line at construction; tests and any future multi-tenant embedding can pass
# their own instance instead.
_DEFAULT_REGISTRY = RunScopedCacheRegistry()


def default_run_scoped_cache_registry() -> RunScopedCacheRegistry:
    """Return the process-wide registry."""
    return _DEFAULT_REGISTRY
