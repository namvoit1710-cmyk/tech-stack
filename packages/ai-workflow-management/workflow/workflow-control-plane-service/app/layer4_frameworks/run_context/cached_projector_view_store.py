"""Cached projector-backed view store — Phase 4c.2.

The Phase 4c.1 ``ProjectorRunContextViewStore`` rebuilds a run's
``ProjectedRunState`` from scratch on every ``get()`` — it walks the
entire event log for the run each time. That works for tests and
low-query workloads but rules out production wiring for read paths
like ``get_run_status`` that may be called every few seconds.

This store caches the projected state **per run** and, on each
subsequent ``get()``, only asks the event log for envelopes whose
``sequence`` is strictly greater than the cached sequence.
``IEventLogRepository.list_by_run_since`` is backed by the ordered
primary-key index in both the memory and SQLite backends, so:

- **Warm cache, no new events** (the hot path for idle runs): one
  index lookup returns an empty list → O(1) amortised ``get()``.
- **Warm cache with Δ new events**: one index lookup + ``Δ`` reducer
  folds → O(Δ) get().
- **Cold cache** (first ``get()`` for a run): full rebuild via
  ``list_by_run`` + full fold → O(|events|), same as the direct
  ``ProjectorRunContextViewStore``. Amortised across many get()s,
  this is negligible.

Cache coherence caveat:
    The cache assumes the underlying log is **append-only**. If a
    caller invokes ``IEventLogRepository.clear(run_id)`` (test
    teardown, Phase 4b.3 Projector rebuild path), the cache does not
    automatically detect it and will continue to serve stale state
    until ``invalidate(run_id)`` is called or the process restarts.
    Production use is append-only, so this is a test-time concern only.
    The ``invalidate`` method is provided for explicit cache control.

Concurrency:
    The dictionary is guarded by a ``threading.Lock``. The engine's
    publish path is already single-threaded per run (one ``asyncio``
    loop per process), so contention is rare; the lock is cheap
    insurance and matches the pattern used by the memory repositories
    in this codebase.

Lifetime (M1 — SA-2258):
    This store holds THREE per-run dicts — ``_cache``, ``_snap_watermark``
    and ``_snap_history`` — and until SA-2258 none of them were ever
    released in production: every run this pod ever projected stayed
    resident until restart. ``invalidate()`` existed but nothing in
    ``app/`` called it, and even if something had, it only popped
    ``_cache`` and left the other two behind.

    Two mechanisms now bound it, in this order:

    1. **Terminal eviction** (the real fix) — the run's terminal
       transition calls ``evict_run``, releasing all three at once. The
       store finds out via ``RunScopedCacheRegistry``, which it JOINS
       explicitly rather than waiting to be discovered by a duck-typed
       scan over someone else's private attribute; being invisible to
       that scan is precisely why this leak survived.
    2. **LRU backstop** — a hard cap for runs that never reach a terminal
       state (crash, cancel-in-flight, an event lost before the
       transition). The cap is on RUN COUNT and is deliberately **not**
       time-based: a TTL would evict a still-hot run and force the cold
       full-log replay at ``get()``, trading a leak for a latency
       regression.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from app.layer2_application.interfaces.cache_budget_registry import (
    default_cache_budget_registry,
)
from app.layer2_application.interfaces.run_scoped_cache_registry import (
    RunScopedCacheRegistry,
    default_run_scoped_cache_registry,
)
from app.layer4_frameworks.caching.bounded_ttl_cache import env_int

# Backstop only. Terminal eviction is what normally frees a run; this bounds
# the runs that never get one. Sized in RUNS, not bytes — a ProjectedRunState
# varies wildly, so a byte budget here would be a guess dressed as a number.
_PROJECTOR_CACHE_MAX_RUNS = env_int("PROJECTOR_CACHE_MAX_RUNS", 512, minimum=1)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

from app.layer1_domain.run_projection import ProjectedRunState
from app.layer2_application.interfaces.event_log_repository_interface import (
    IEventLogRepository,
)
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextView,
    IRunContextViewStore,
)
from app.layer2_application.interfaces.snapshot_repository_interface import (
    ISnapshotRepository,
    Snapshot,
)
from app.layer4_frameworks.run_context.projector_view_store import ProjectedView

if TYPE_CHECKING:
    from app.layer2_application.interfaces.monitor_interface import IMonitor
    from app.layer2_application.repositories.run_repository_interface import (
        IRunRepository,
    )
    from app.layer2_application.repositories.workflow_repository_interface import (
        IWorkflowRepository,
    )


_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})

#: Every way :meth:`get` can end, enumerated rather than accumulated as they
#: happen. The counters therefore exist at 0 from boot -- a panel that shows
#: "No data" until the first cold replay is a panel nobody can alert on, and the
#: whole point of these is to catch the expensive read the FIRST time it fires.
_LOAD_OUTCOMES = (
    "warm",               # cached and no new events -- the cheapest read there is
    "warm_delta",         # cached, N new envelopes folded in
    "cold_snapshot",      # cache miss, primed from a snapshot + tail replay
    "cold_replay",        # cache miss, no usable snapshot -> FULL event-log replay
    "aggregate_fallback", # no events at all, served from the run aggregate
    "missing",            # nothing to serve
    "error",              # the read raised
)

#: Latency of one ``get``, labelled by outcome. This is a genuine duration, so
#: the module's default latency buckets fit it -- unlike the count-valued
#: histograms elsewhere in this service, which carry the same buckets and put
#: every real observation in +Inf.
LOAD_HISTOGRAM = "projector_view_load_seconds"
_logger = logging.getLogger(__name__)


class CachedProjectorRunContextViewStore(IRunContextViewStore):
    """Projector-backed view store with per-run incremental cache.

    Use this as a drop-in replacement for
    ``ProjectorRunContextViewStore`` whenever query-side code is
    hot-pathed and full rebuilds would dominate.

    Phase 4c.3 extension: optional ``workflow_repo`` and ``run_repo``
    parameters enable the hybrid fallback model. ``workflow_repo`` is
    threaded into every ``ProjectedView`` construction so that
    ``topology`` / ``entry_nodes`` reads populate from
    ``TopologyBuilder.prepare``. ``run_repo`` enables a real
    ``get_by_root_id`` implementation that discovers parent/child
    run ids via the run repo and replays each run's event log via
    ``self.get(run_id)`` (which benefits from the cache).
    """

    def __init__(
        self,
        event_log_repo: IEventLogRepository,
        workflow_repo: IWorkflowRepository | None = None,
        run_repo: IRunRepository | None = None,
        *,
        snapshot_repo: ISnapshotRepository | None = None,
        snapshot_mode: str = "off",
        snapshot_every_n_events: int = 100,
        snapshot_every_t_seconds: int = 300,
        snapshot_keep_k_back: int = 2,
        clock: Callable[[], float] | None = None,
        monitor: "IMonitor | None" = None,
        cache_registry: RunScopedCacheRegistry | None = None,
        max_runs: int | None = None,
        budget_registry: Any = None,
        schema_dictionary: Any = None,
    ) -> None:
        self._event_log_repo = event_log_repo
        self._workflow_repo = workflow_repo
        # AIW_SCHEMA_DEF lookup, used only under SCHEMA_REF_MODE=on to put back
        # the schema a thin ref left on the wire. Optional: absent in every test
        # that predates it, and every ref is complete without it today.
        self._schema_dictionary = schema_dictionary
        self._run_repo = run_repo
        self._cache: dict[str, tuple[ProjectedRunState, int]] = {}
        self._lock = threading.Lock()
        # Recency order over the SAME run_ids the three dicts are keyed by, so
        # one overflow eviction releases all three together instead of leaving
        # a run half-resident.
        self._lru: OrderedDict[str, None] = OrderedDict()
        self._max_runs = (
            _PROJECTOR_CACHE_MAX_RUNS if max_runs is None else max(1, int(max_runs))
        )
        self._evictions_terminal = 0
        self._evictions_overflow = 0
        # Read-path accounting. Measured on tenant-1 2026-08-13: a single read of
        # one existing run cost a 185 MiB transient peak with NO run started and
        # no artifact I/O at all -- the whole cost was replay plus variable
        # resolution. Until this landed the bucket exported occupancy only, so a
        # cheap warm read and a full event-log replay were indistinguishable, and
        # "the read path is expensive" could not be turned into a threshold.
        self._loads: dict[str, int] = {o: 0 for o in _LOAD_OUTCOMES}
        # A plain counter, deliberately NOT a histogram: this module's histograms
        # carry LATENCY buckets (0.001s..5.0s), so a count-valued one would put
        # every real value in +Inf. Mean folds = envelopes_replayed / the loads
        # that fold, computed in the query.
        self._envelopes_replayed = 0

        # Phase 4 — snapshot store hooks. When ``snapshot_repo`` is
        # ``None`` or ``snapshot_mode == "off"``, all snapshot logic is
        # a no-op; cold/warm paths behave identically to pre-Phase-4.
        self._snapshot_repo = snapshot_repo
        self._snapshot_mode = (snapshot_mode or "off").lower()
        self._snap_every_n = max(1, int(snapshot_every_n_events))
        self._snap_every_t = max(1, int(snapshot_every_t_seconds))
        self._snap_keep_k = max(1, int(snapshot_keep_k_back))
        self._clock = clock or time.monotonic
        # Per-run watermark: (last_snapshot_sequence, last_snapshot_time).
        self._snap_watermark: dict[str, tuple[int, float]] = {}
        # Per-run deque of last K saved sequences; enables retention
        # without adding a ``list_sequences`` call to the repo interface.
        self._snap_history: dict[str, deque[int]] = {}
        self._monitor = monitor

        # Announce ourselves so terminal runs actually reach us. Passing a
        # registry explicitly (tests, embedding) overrides the process-wide one;
        # ``register`` holds a weak reference, so this never extends our life.
        self._cache_registry = (
            default_run_scoped_cache_registry()
            if cache_registry is None else cache_registry
        )
        self._cache_registry.register(self, name="CachedProjectorRunContextViewStore")
        # And report into the memory budget (SA-2256) — the three dicts above
        # are the budget's largest run-scoped line after the materialize cache.
        (default_cache_budget_registry() if budget_registry is None
         else budget_registry).register(self, "projector_view_store")

    def _view(self, state: ProjectedRunState) -> IRunContextView:
        """The ONE place a view is constructed — and where a hash-only ref is
        made whole again.

        ``SCHEMA_REF_MODE=on`` strips ``columns``/``column_types`` from refs on
        the wire, where they are 90.4% of every descriptor's bytes. They have to
        come back before anything reads them, and the resolver is the wrong
        place to do it: expanding inside ``FileBackedResolverStrategy`` only
        helps refs that pass through the resolver, while ``node_data_projection``,
        ``edit_node_data``, ``data_edit_handler``, ``schema_artifact_provisioner``
        and ``data_store_service`` read a ref's columns without going near it —
        19 sites across six modules (§19.2).

        Here instead, because this is where ``variables`` becomes visible to all
        of them. It is the same job this store already does for ``topology``,
        which the pure reducer likewise cannot fetch: ``ProjectedRunState.apply``
        takes an envelope and nothing else, so a value needing I/O is filled in
        by the layer that has it.

        Never raises: a dictionary that cannot answer leaves the ref exactly as
        it arrived, which for an inline ref is already complete.
        """
        try:
            from app.layer2_application.data import schema_dictionary as _sd

            if self._schema_dictionary is not None and _sd.enforcing():
                for value in (state.variables or {}).values():
                    if isinstance(value, dict) and value.get("__file_ref"):
                        _sd.expand(value, self._schema_dictionary)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("schema expansion skipped for a view: %s", exc)
        return ProjectedView(state, workflow_repo=self._workflow_repo)

    def get(self, run_id: str) -> IRunContextView | None:
        """Return a read-only view of ``run_id`` from the cached projection.

        Cold path: full rebuild from ``list_by_run``; cache populated.
        Warm path: ``list_by_run_since`` with cached sequence; zero or
        Δ envelopes folded in.
        Missing path: repo has no events for ``run_id`` → returns ``None``.

        Every returned view is wrapped with ``workflow_repo`` so that
        ``topology`` / ``entry_nodes`` reads trigger the Phase 4c.3
        fallback if a workflow_repo is wired.

        Timed and counted by outcome. ``error`` is the initial value on purpose:
        if :meth:`_load` raises, the ``finally`` still records, and recording it
        as ``missing`` would quietly file a failure as a cache result.
        """
        started = self._clock()
        outcome, folded = "error", 0
        try:
            view, outcome, folded = self._load(run_id)
            return view
        finally:
            self._record_load(outcome, self._clock() - started, folded)

    def _load(self, run_id: str) -> tuple["IRunContextView | None", str, int]:
        """The read itself. Returns ``(view, outcome, envelopes_folded)``."""
        with self._lock:
            cached = self._cache.get(run_id)
            if cached is not None and run_id in self._lru:
                # A READ counts as use. Without this the backstop degenerates
                # into evict-by-last-write, so a hot but rarely-appended run is
                # dropped and its next get() pays a cold full-log replay —
                # precisely the latency regression the cap exists to avoid.
                self._lru.move_to_end(run_id)

        if cached is not None:
            state, cached_sequence = cached
            new_events = self._event_log_repo.list_by_run_since(
                run_id, cached_sequence,
            )
            if not new_events:
                return self._view(state), "warm", 0

            for envelope in new_events:
                state = state.apply(envelope)
            new_sequence = new_events[-1].sequence

            with self._lock:
                self._store_locked(run_id, state, new_sequence)
            self._maybe_snapshot(run_id, state, new_sequence)
            return self._view(state), "warm_delta", len(new_events)

        # Cold cache — try snapshot + tail replay first, fall back to
        # full-log replay on miss/schema-mismatch/decode failure.
        primed = self._try_cold_load_from_snapshot(run_id)
        if primed is not None:
            state, last_sequence, tail_folded = primed
            with self._lock:
                self._store_locked(run_id, state, last_sequence)
            self._maybe_snapshot(run_id, state, last_sequence)
            return self._view(state), "cold_snapshot", tail_folded

        envelopes = self._event_log_repo.list_by_run(run_id)
        if not envelopes:
            # No events yet — fall back to the aggregate if a run_repo
            # is wired. Covers the pre-first-emission window (brand-new
            # runs before ``RunStarted`` projects) and test fixtures that
            # populate ``MemoryRunRepository`` directly without an event
            # loop. Production runs always have at least ``RunStarted``
            # so this is only hit on fresh state. Returns ``None`` when
            # there's no run_repo or the run is unknown, preserving the
            # prior contract.
            view = self._aggregate_fallback(run_id)
            # Separated because they cost the same and mean opposite things: one
            # is a brand-new run before its first event, the other is a read for
            # a run that does not exist.
            return view, ("aggregate_fallback" if view is not None else "missing"), 0

        state = ProjectedRunState(run_id=run_id)
        for envelope in envelopes:
            state = state.apply(envelope)
        last_sequence = envelopes[-1].sequence

        # Phase 4.6 — shadow-mode parity check: compare the snapshot
        # path against the authoritative full-log replay. Drift is
        # logged and counted; the full-replay result wins.
        self._maybe_shadow_compare(run_id, state)

        with self._lock:
            self._store_locked(run_id, state, last_sequence)
        self._maybe_snapshot(run_id, state, last_sequence)
        return self._view(state), "cold_replay", len(envelopes)

    def _record_load(self, outcome: str, elapsed: float, folded: int) -> None:
        """Count the read and time it. Never lets metrics break a read."""
        with self._lock:
            self._loads[outcome] = self._loads.get(outcome, 0) + 1
            self._envelopes_replayed += folded
        if self._monitor is None:
            return
        try:
            # Always the SAME label key. prometheus_client freezes a family's
            # label set on first observation and drops mismatched samples
            # silently, so an occasionally-omitted tag loses data with no error.
            self._monitor.histogram(
                LOAD_HISTOGRAM, elapsed, {"outcome": outcome},
            )
        except Exception:  # pragma: no cover - never let monitor break reads
            pass

    def get_by_root_id(self, root_id: str) -> list[IRunContextView]:
        """Return views for every run rooted at ``root_id``.

        Phase 4c.3: when ``run_repo`` is supplied, delegates to
        ``run_repo.get_by_root_id`` to discover run ids and replays
        each via ``self.get(run_id)`` — every individual replay
        benefits from the cache, so a subsequent root-id query on the
        same parent is O(|children|) cache lookups rather than
        O(|children| × |events|) rebuilds.

        Without a run_repo wired, raises ``NotImplementedError`` to
        preserve the Phase 4c.2 contract.
        """
        if self._run_repo is None:
            raise NotImplementedError(
                "CachedProjectorRunContextViewStore.get_by_root_id requires "
                "a run_repo fallback to discover parent/child run ids. "
                "Pass ``run_repo`` in the constructor (Phase 4c.3) or "
                "use LiveRunContextViewStore for features that need "
                "parent/child run traversal without the fallback."
            )
        runs = self._run_repo.get_by_root_id(root_id)
        views: list[IRunContextView] = []
        for run in runs:
            view = self.get(run.id)
            if view is not None:
                views.append(view)
        return views

    # ──────────────────── aggregate fallback ─────────────────────

    def _aggregate_fallback(self, run_id: str) -> IRunContextView | None:
        """Project a ``WorkflowRun`` aggregate into an ``IRunContextView``.

        Used only when the event log has no envelopes for ``run_id``.
        Returns ``None`` when no ``run_repo`` is wired or the run does
        not exist in the aggregate. The fallback is never cached — once
        events arrive, ``get`` will fall through to the replay path and
        the cache is populated from the event log.
        """
        if self._run_repo is None:
            return None
        run = self._run_repo.get_by_id(run_id)
        if run is None:
            return None
        from app.layer1_domain.entities.run_context_snapshot import (
            RunContextSnapshot,
        )
        return RunContextSnapshot.from_run(run)

    # ─────────────────────── cache management ────────────────────

    def _store_locked(
        self, run_id: str, state: ProjectedRunState, sequence: int,
    ) -> None:
        """Cache a run's state and enforce the LRU backstop. Lock held."""
        self._cache[run_id] = (state, sequence)
        self._lru[run_id] = None
        self._lru.move_to_end(run_id)
        while len(self._lru) > self._max_runs:
            oldest, _ = self._lru.popitem(last=False)
            self._release_locked(oldest)
            self._evictions_overflow += 1
            _logger.debug(
                "projector cache overflow — evicted run %s (cap=%d). A run "
                "reaching this path never had a terminal transition.",
                oldest, self._max_runs,
            )

    def _release_locked(self, run_id: str) -> bool:
        """Drop ALL per-run state for ``run_id``. Lock held.

        All three dicts, always. ``invalidate`` used to pop only ``_cache``,
        so even a caller doing the right thing still leaked the snapshot
        watermark and history.
        """
        found = self._cache.pop(run_id, None) is not None
        found = self._snap_watermark.pop(run_id, None) is not None or found
        found = self._snap_history.pop(run_id, None) is not None or found
        self._lru.pop(run_id, None)
        return found

    def evict_run(self, run_id: str) -> None:
        """Release every per-run structure for ``run_id`` (RunScopedCache).

        Called by ``RunScopedCacheRegistry`` when a run reaches a terminal
        state. Idempotent — a terminal transition can fire more than once,
        and a run that was never cached is not an error.
        """
        if not run_id:
            return
        with self._lock:
            if self._release_locked(run_id):
                self._evictions_terminal += 1

    def invalidate(self, run_id: str) -> None:
        """Drop the cached projection for ``run_id``.

        Callers should invoke this after ``IEventLogRepository.clear``
        (e.g., Phase 4b.3 rebuild-from-wipe test) or whenever the
        append-only invariant is temporarily broken.

        Releases the snapshot watermark and history too: leaving them behind
        would let a rebuilt run inherit the pre-wipe watermark and skip its
        next snapshot.
        """
        with self._lock:
            self._release_locked(run_id)

    def invalidate_all(self) -> None:
        """Drop every cached projection — intended for test teardown."""
        with self._lock:
            self._cache.clear()
            self._snap_watermark.clear()
            self._snap_history.clear()
            self._lru.clear()

    def cache_size(self) -> int:
        """Number of runs currently cached. Used by tests and monitoring."""
        with self._lock:
            return len(self._cache)

    def budget_snapshot(self) -> dict:
        """Report into the SA-2256 memory budget (CacheBudgetRegistry).

        ``cache`` is renamed to ``entries`` so every bucket in the export
        answers "how many entries" under the same metric name; a dashboard
        should not need to know each cache's internal vocabulary.
        """
        sizes = dict(self.cache_sizes())
        sizes["entries"] = sizes.pop("cache")
        return sizes

    def cache_sizes(self) -> dict[str, int]:
        """Per-structure occupancy for the SA-2256 memory-budget export.

        Reported separately rather than as one number: if these three ever
        drift apart, something is releasing one dict and not the others —
        which is exactly the shape of the bug this replaced.
        """
        with self._lock:
            loads = dict(self._loads)
            sizes = {
                "cache": len(self._cache),
                "snap_watermark": len(self._snap_watermark),
                "snap_history": len(self._snap_history),
                "lru": len(self._lru),
                "max_runs": self._max_runs,
                "evictions_terminal": self._evictions_terminal,
                "evictions_overflow": self._evictions_overflow,
                # hits/misses under the names every OTHER bucket already uses,
                # so wcp_cache_hits{bucket="projector_view_store"} means here
                # what it means everywhere else and a dashboard needs no special
                # case. The per-outcome breakdown sits alongside rather than
                # replacing them: "miss" is one word for two paths whose cost
                # differs by orders of magnitude.
                "hits": loads["warm"] + loads["warm_delta"],
                "misses": loads["cold_snapshot"] + loads["cold_replay"],
                "envelopes_replayed": self._envelopes_replayed,
            }
        sizes.update({f"loads_{outcome}": loads[outcome]
                      for outcome in _LOAD_OUTCOMES})
        return sizes

    # ─────────────────────── snapshot hooks (Phase 4) ────────────────

    def _maybe_shadow_compare(
        self, run_id: str, full_replay_state: ProjectedRunState,
    ) -> None:
        """Under ``SNAPSHOT_MODE=shadow``, replay from the snapshot and
        diff against the full-log state. Drift fields are logged and
        emitted as monitor counters; the full-replay state still wins.

        Any exception (load failure, decode error) is swallowed — the
        shadow path must never break the authoritative read.
        """
        if self._snapshot_repo is None or self._snapshot_mode != "shadow":
            return
        try:
            snap = self._snapshot_repo.load_latest(
                run_id, min_schema_version=ProjectedRunState.SCHEMA_VERSION,
            )
            if snap is None:
                self._emit_drift_counter("no_snapshot", run_id)
                return
            snap_state = ProjectedRunState.from_json(snap.state_json)
            tail = self._event_log_repo.list_by_run_since(run_id, snap.sequence)
            for envelope in tail:
                snap_state = snap_state.apply(envelope)
            diverged = snap_state.diff(full_replay_state)
            if diverged:
                _logger.warning(
                    "snapshot shadow parity drift for run %s: fields=%s",
                    run_id, diverged,
                )
                self._emit_drift_counter(
                    "diverged", run_id, fields=",".join(diverged),
                )
            else:
                self._emit_drift_counter("ok", run_id)
        except Exception as exc:  # pragma: no cover — defensive
            _logger.warning(
                "snapshot shadow compare failed for run %s: %s", run_id, exc,
            )
            self._emit_drift_counter("error", run_id)

    def _emit_drift_counter(
        self, outcome: str, run_id: str, **extra: str,
    ) -> None:
        if self._monitor is None:
            return
        tags = {"outcome": outcome, "run_id": run_id}
        tags.update(extra)
        try:
            self._monitor.increment("snapshot_drift", tags=tags)
        except Exception:  # pragma: no cover — never let monitor break reads
            pass

    def _try_cold_load_from_snapshot(
        self, run_id: str,
    ) -> tuple[ProjectedRunState, int, int] | None:
        """Attempt to prime the cache from the latest snapshot.

        Returns ``(state, last_sequence, tail_envelopes_folded)`` on success --
        the third element is what makes a snapshot-assisted read comparable to a
        full replay rather than merely cheaper-looking. ``None`` when:
        - snapshot mode is not ``authoritative``,
        - no snapshot repo is wired,
        - no snapshot at the current schema version exists for the run,
        - the snapshot JSON fails to decode,
        - no events exist for the run at all (caller returns ``None``).
        """
        if self._snapshot_repo is None or self._snapshot_mode != "authoritative":
            return None
        try:
            snap = self._snapshot_repo.load_latest(
                run_id, min_schema_version=ProjectedRunState.SCHEMA_VERSION,
            )
        except Exception as exc:  # pragma: no cover — defensive
            _logger.warning(
                "snapshot load failed for run %s, falling back to full replay: %s",
                run_id, exc,
            )
            return None
        if snap is None:
            return None
        try:
            state = ProjectedRunState.from_json(snap.state_json)
        except Exception as exc:
            _logger.warning(
                "snapshot decode failed for run %s @ seq %d, falling back: %s",
                run_id, snap.sequence, exc,
            )
            return None

        # Tail replay from (snap.sequence, +∞).
        tail = self._event_log_repo.list_by_run_since(run_id, snap.sequence)
        for envelope in tail:
            state = state.apply(envelope)
        last_sequence = tail[-1].sequence if tail else snap.sequence
        # Arm the watermark so we don't immediately re-snapshot at the
        # same sequence we just loaded from.
        self._snap_watermark.setdefault(run_id, (snap.sequence, self._clock()))
        return state, last_sequence, len(tail)

    def _maybe_snapshot(
        self, run_id: str, state: ProjectedRunState, sequence: int,
    ) -> None:
        """Persist a snapshot if the threshold / terminal policy fires.

        Failures are swallowed with a warning — a snapshot miss falls
        back to full-log replay on the next cold start, which is
        correct just slow. Identical to ``SNAPSHOT_MODE=off`` behaviour.
        """
        if self._snapshot_repo is None or self._snapshot_mode == "off":
            return
        if not self._should_snapshot(run_id, state, sequence):
            return
        try:
            self._save_snapshot_with_retention(run_id, state, sequence)
        except Exception as exc:  # pragma: no cover — defensive
            _logger.warning(
                "snapshot write failed for run %s @ seq %d: %s",
                run_id, sequence, exc,
            )

    def _should_snapshot(
        self, run_id: str, state: ProjectedRunState, sequence: int,
    ) -> bool:
        """Fire on terminal transition OR N-events OR T-seconds."""
        last_seq, last_time = self._snap_watermark.get(run_id, (0, 0.0))
        if sequence <= last_seq:
            return False  # nothing new to snapshot
        if state.status in _TERMINAL_STATUSES:
            return True
        if sequence - last_seq >= self._snap_every_n:
            return True
        now = self._clock()
        if last_time and now - last_time >= self._snap_every_t:
            return True
        # If this is the first snapshot for a run, arm the T-timer
        # without forcing an immediate write.
        if last_time == 0.0:
            self._snap_watermark[run_id] = (last_seq, now)
        return False

    def _save_snapshot_with_retention(
        self, run_id: str, state: ProjectedRunState, sequence: int,
    ) -> None:
        """Persist the snapshot and enforce K-back retention."""
        assert self._snapshot_repo is not None  # _maybe_snapshot guards
        snapshot = Snapshot(
            run_id=run_id,
            sequence=sequence,
            schema_version=ProjectedRunState.SCHEMA_VERSION,
            state_json=state.to_json(),
            created_at=_now_iso(),
        )
        self._snapshot_repo.save(snapshot)

        history = self._snap_history.setdefault(run_id, deque())
        history.append(sequence)
        while len(history) > self._snap_keep_k:
            history.popleft()
        if len(history) > 0:
            self._snapshot_repo.delete_older_than(run_id, history[0])

        self._snap_watermark[run_id] = (sequence, self._clock())
