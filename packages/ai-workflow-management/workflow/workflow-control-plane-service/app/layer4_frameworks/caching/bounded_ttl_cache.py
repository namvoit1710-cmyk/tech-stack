"""A cache that cannot outgrow its budget — LRU cap first, TTL second.

Why both, and in that order
---------------------------
SA-2254 asks the control plane to run in 256MB. Four caches (M1-M4) grow
without bound today, and their growth tracks **pod uptime**, not load, so a
short test never sees it.

The obvious fix — "add a TTL" — does not actually bound memory when the key
space is unbounded and expiry is lazy. ``MetadataStampingPublisher`` is keyed
by ``run_id``: if 10 000 distinct runs publish inside one TTL window, all
10 000 entries are resident, and a lazily-expiring TTL never visits the ones
nobody reads again. The acceptance criterion "10k distinct run_ids => bounded
memory" fails against a TTL-only cache.

So the cap is what bounds MEMORY and the TTL is what bounds STALENESS. They
answer different questions and this class always applies both:

* ``maxsize``     — hard ceiling on resident entries. Least-recently-used is
  evicted on overflow. This is the memory guarantee.
* ``ttl_seconds`` — how long an entry may be believed. This is the correctness
  guarantee (``EventEnricher`` served a workflow edited hours ago for the life
  of the pod; that is M4's second, non-memory defect).

Operational notes
-----------------
* ``maxsize=0`` disables caching entirely — a kill switch that needs no deploy.
* ``ttl_seconds=0`` disables expiry and leaves a pure LRU, for values that are
  genuinely immutable.
* The clock is ``time.monotonic``, not ``time.time``: a wall-clock jump (NTP
  step, container migration) must not resurrect or mass-expire entries.
* ``stats()`` exists so SA-2256 can export every bucket in the memory budget
  without each cache inventing its own accessor.

Thread-safe: the publish path this sits on is hit from the orchestration
worker threads.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from app.layer2_application.interfaces.cache_budget_registry import (
    default_cache_budget_registry,
)
from app.layer2_application.interfaces.run_scoped_cache_registry import (
    default_run_scoped_cache_registry,
)

logger = logging.getLogger(__name__)


def env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read a cache bound from the environment, tolerating garbage.

    Every bound in this file is an operational dial, so it must be tunable
    without a code change — and a typo in that dial must not stop the pod from
    booting. Lives here because "bounded cache" and "how its bound is
    configured" are the same concern.
    """
    raw = os.getenv(name, "")
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r is not an integer — using %d", name, raw, default)
        return max(minimum, default)

# Sentinel for "not present". A cached value may legitimately be falsy — the
# metadata cache stores ``{}`` for a run with no metadata — so ``None`` and
# truthiness are both unusable as miss signals. This bug (treating a cached
# empty dict as a miss) is easy to reintroduce; the sentinel makes it impossible.
MISS: Any = object()


# ── the sweep list (SA-2368) ──────────────────────────────────────────────
#
# ``purge_expired`` has existed since SA-2254 and, until this landed, was called
# from nowhere in the service. That is what made the module docstring's own
# warning bite: an entry whose key is never read again is never visited, so a
# 300s TTL bounded *staleness* while the VALUE stayed resident until the LRU cap
# pushed it out. Measured on tenant-1 2026-08-12: the ``run_credentials`` bucket
# held 74 entries with ``evictions=0`` after 13.5h, so every credential decrypted
# since boot was still in memory — a residency of ~3.9 days against a 300s TTL.
#
# The list lives HERE rather than on the budget registry because expiry is this
# module's own semantics; the budget registry is a pull-only observability
# surface and giving it write access to its members would blur that. Members are
# weak for the same reason both registries are: a sweeper that pinned every
# cache it swept would be a leak introduced by the leak fix.
_SWEEPABLE: list[tuple["weakref.ReferenceType", str]] = []
_SWEEP_LOCK = threading.Lock()


def _join_sweep(cache: Any, name: str) -> None:
    with _SWEEP_LOCK:
        _SWEEPABLE[:] = [(r, n) for r, n in _SWEEPABLE if r() is not None]
        for ref, _ in _SWEEPABLE:
            if ref() is cache:
                return
        _SWEEPABLE.append((weakref.ref(cache), name))


def sweepable_names() -> list[str]:
    """Names of the caches a sweep would visit. For tests and diagnostics."""
    with _SWEEP_LOCK:
        _SWEEPABLE[:] = [(r, n) for r, n in _SWEEPABLE if r() is not None]
        return [n for _, n in _SWEEPABLE]


def purge_all_expired() -> dict[str, int]:
    """Drop expired entries from every named cache. ``{name: dropped}``.

    Never raises. One cache that fails must not cost the others their sweep —
    the same isolation rule ``RunScopedCacheRegistry.release_run`` follows, and
    for the same reason: a cleanup pass that aborts halfway leaves the rest
    holding their entries forever.
    """
    with _SWEEP_LOCK:
        _SWEEPABLE[:] = [(r, n) for r, n in _SWEEPABLE if r() is not None]
        snapshot = list(_SWEEPABLE)
    dropped: dict[str, int] = {}
    for ref, name in snapshot:
        cache = ref()
        if cache is None:
            continue
        try:
            n = cache.purge_expired()
        except Exception:
            logger.warning(
                "cache %s failed to purge — continuing so the remaining "
                "caches are still swept", name, exc_info=True,
            )
            continue
        if n:
            dropped[name] = dropped.get(name, 0) + n
    return dropped


@dataclass(frozen=True)
class CacheStats:
    """A snapshot of one cache bucket, shaped for the SA-2256 metrics export."""

    name: str
    size: int
    maxsize: int
    hits: int
    misses: int
    evictions: int
    expirations: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


class BoundedTTLCache:
    """An LRU-capped, TTL-expiring, thread-safe mapping.

    Memory is O(maxsize) regardless of traffic, key cardinality or uptime.
    """

    # ``__weakref__`` is required: the budget and run-scoped registries hold
    # members weakly, and __slots__ otherwise makes this class un-weak-referenceable.
    __slots__ = ("_name", "_maxsize", "_ttl", "_clock", "_store", "_lock",
                 "_hits", "_misses", "_evictions", "_expirations", "__weakref__")

    def __init__(
        self,
        maxsize: int,
        ttl_seconds: float,
        name: str = "",
        clock: Callable[[], float] = time.monotonic,
        budget_registry: Any = None,
    ) -> None:
        self._name = name
        self._maxsize = max(0, int(maxsize))
        self._ttl = max(0.0, float(ttl_seconds))
        self._clock = clock
        # key -> (value, expires_at). Ordered by recency of use.
        self._store: OrderedDict[Any, tuple[Any, float]] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._expirations = 0
        # A NAMED cache joins the memory budget automatically — an unobserved
        # cache is how SA-2254's four leaks stayed invisible. Anonymous caches
        # (tests, throwaways) stay out so the export is not noise.
        if name:
            registry = (
                default_cache_budget_registry()
                if budget_registry is None else budget_registry
            )
            registry.register(self, name)
            # …and the sweep, on the same gate. Joined regardless of ``_ttl``:
            # ``put(ttl_seconds=…)`` gives an entry its own expiry, so even a
            # pure-LRU cache (``ttl_seconds=0``) can hold expiring entries —
            # which is exactly what the credential path does.
            _join_sweep(self, name)

    # -- reads -----------------------------------------------------------

    def get(self, key: Any, default: Any = MISS) -> Any:
        """Return the live value for *key*, or *default* (``MISS`` by default)."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return default
            value, expires_at = entry
            if self._is_expired(expires_at):
                del self._store[key]
                self._expirations += 1
                self._misses += 1
                return default
            self._store.move_to_end(key)
            self._hits += 1
            return value

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not MISS

    def __len__(self) -> int:
        return self.size()

    def __bool__(self) -> bool:
        """A cache object is always truthy, empty or not.

        Without this, ``__len__`` makes an empty cache falsy, so the familiar
        ``self._cache = injected or BoundedTTLCache(...)`` silently discards an
        injected-but-empty cache and builds a default one instead. Same class
        of bug as treating a cached ``{}`` as a miss — worth closing here once
        rather than at every call site.
        """
        return True

    def keys(self) -> list:
        """Live (unexpired) keys, most-recently-used last."""
        with self._lock:
            self._purge_expired_locked()
            return list(self._store.keys())

    def __iter__(self) -> Iterator:
        return iter(self.keys())

    # -- writes ----------------------------------------------------------

    def put(self, key: Any, value: Any, ttl_seconds: float | None = None) -> None:
        """Store *value*, evicting the least-recently-used entry if full.

        ``ttl_seconds`` overrides the cache default for this entry — that is
        how a negative result gets a deliberately short life without a second
        cache object.
        """
        if self._maxsize == 0:
            return  # caching disabled
        ttl = self._ttl if ttl_seconds is None else max(0.0, float(ttl_seconds))
        expires_at = 0.0 if ttl == 0.0 else self._clock() + ttl
        with self._lock:
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expires_at)
            while len(self._store) > self._maxsize:
                self._store.popitem(last=False)
                self._evictions += 1

    def invalidate(self, key: Any) -> bool:
        """Drop one entry. Returns True if something was actually removed."""
        with self._lock:
            return self._store.pop(key, None) is not None

    def clear(self) -> None:
        """Release every entry. Counters are kept — they are lifetime totals."""
        with self._lock:
            self._store.clear()

    def purge_expired(self) -> int:
        """Drop every expired entry now. Returns how many went."""
        with self._lock:
            return self._purge_expired_locked()

    # -- observability ---------------------------------------------------

    def size(self) -> int:
        """Resident entries, expired-but-not-yet-collected included."""
        with self._lock:
            return len(self._store)

    def budget_snapshot(self) -> dict:
        """Report into the SA-2256 memory budget (CacheBudgetRegistry)."""
        s = self.stats()
        return {
            "entries": s.size,
            "maxsize": s.maxsize,
            "hits": s.hits,
            "misses": s.misses,
            "evictions": s.evictions,
            "expirations": s.expirations,
        }

    def stats(self) -> CacheStats:
        with self._lock:
            return CacheStats(
                name=self._name,
                size=len(self._store),
                maxsize=self._maxsize,
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                expirations=self._expirations,
            )

    # -- internals -------------------------------------------------------

    def _is_expired(self, expires_at: float) -> bool:
        return expires_at != 0.0 and expires_at <= self._clock()

    def _purge_expired_locked(self) -> int:
        dead = [k for k, (_, exp) in self._store.items() if self._is_expired(exp)]
        for k in dead:
            del self._store[k]
        self._expirations += len(dead)
        return len(dead)


class RunScopedTTLCache(BoundedTTLCache):
    """A ``BoundedTTLCache`` that also dies with its run (SA-2368).

    Why a subclass and not a method on the base
    -------------------------------------------
    ``RunScopedCacheRegistry.register`` gates on ``hasattr(cache, "evict_run")``.
    Putting ``evict_run`` on ``BoundedTTLCache`` would make that check true for
    every bucket in the service — including the six whose keys are not runs at
    all — and turn a meaningful guard into one that always passes. Here the check
    means what it says: only a cache that can name the run behind a key can join.

    The mapper, not a convention
    ----------------------------
    Keys differ per bucket: ``run_credentials`` is keyed
    ``(root_run_id, tenant_id, ref)`` while a metadata cache is keyed by a bare
    ``run_id``. Rather than legislate a tuple layout that every future caller
    must remember, the owner passes ``run_id_of_key`` and keeps its own key
    shape. A key the mapper cannot read is skipped and COUNTED
    (``run_evict_errors``) rather than logged per-key — a silent skip here means
    a retained secret, so it has to show up as a number.
    """

    __slots__ = ("_run_id_of_key", "_run_evictions", "_run_evict_errors")

    def __init__(
        self,
        maxsize: int,
        ttl_seconds: float,
        name: str = "",
        clock: Callable[[], float] = time.monotonic,
        budget_registry: Any = None,
        *,
        run_id_of_key: Callable[[Any], str | None],
        run_scoped_registry: Any = None,
    ) -> None:
        self._run_id_of_key = run_id_of_key
        self._run_evictions = 0
        self._run_evict_errors = 0
        super().__init__(
            maxsize, ttl_seconds, name, clock, budget_registry,
        )
        if name:
            registry = (
                default_run_scoped_cache_registry()
                if run_scoped_registry is None else run_scoped_registry
            )
            registry.register(self, name)

    def evict_run(self, run_id: str) -> int:
        """Drop every entry belonging to *run_id*. Returns how many went.

        Idempotent, and a no-op for a run that was never cached — terminal
        transitions fire more than once (``orchestration_engine`` calls
        ``release_run`` on both the cancel path and the normal completion path).

        O(size) rather than indexed by run: the cap is 512 and this runs once per
        terminal transition, so an index would be a second structure to keep
        correct for no measurable gain.
        """
        if not run_id:
            return 0
        with self._lock:
            dead = []
            errors = 0
            for key in self._store:
                try:
                    if self._run_id_of_key(key) == run_id:
                        dead.append(key)
                except Exception:
                    errors += 1
            for key in dead:
                del self._store[key]
            self._run_evictions += len(dead)
            self._run_evict_errors += errors
            return len(dead)

    def budget_snapshot(self) -> dict:
        """The base snapshot plus the two run-scoped counters.

        ``run_evictions`` is what proves the fix works in production: it must
        climb with run completions, and ``entries`` must stop tracking uptime.
        """
        snap = super().budget_snapshot()
        snap["run_evictions"] = self._run_evictions
        snap["run_evict_errors"] = self._run_evict_errors
        return snap
