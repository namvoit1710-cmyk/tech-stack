"""Per-run scope + lock lifecycle — extracted from OrchestrationEngine.

Owns three pieces of per-run state that previously sat on the engine
and cluttered every ``orchestrate_*`` entry point:

* An ``asyncio.Lock`` per run so concurrent orchestrate calls for the
  same run serialise.
* A pair of contextvar tokens (current run id, current
  ``ResolutionScope``) that wrap every orchestrate call so handlers
  resolving ``$variable.X`` / ``$parent.*`` / ``$root.*`` expressions
  see the right scope chain.
* The resolver-strategy per-run caches that need releasing when a run
  reaches a terminal state.

The class is intentionally narrow. It has no awareness of node
handlers, events, or the orchestration queue — it just needs read
access to runs + workflow snapshots so it can rebuild the
``ResolutionScope`` chain for sub-workflow runs (``$parent`` / ``$root``
expressions walk the chain).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from typing import Any, Optional
from collections.abc import Mapping

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer2_application.resolve.variable_resolver import (
    reset_current_run_id,
    set_current_run_id,
)
from app.layer2_application.definition.workflow_snapshot_loader import (
    load_workflow_for_run as _load_workflow_for_run,
)
from app.layer2_application.interfaces.cache_budget_registry import (
    default_cache_budget_registry,
)
from app.layer2_application.interfaces.run_scoped_cache_registry import (
    default_run_scoped_cache_registry,
)
from app.layer1_domain.value_objects.resolution_scope import (
    ResolutionScope,
    ResolutionScopeBuilder,
    reset_current_scope,
    set_current_scope,
)

_logger = logging.getLogger(__name__)


def _max_run_locks() -> int:
    """Backstop size for ``_run_locks``. Garbage tolerated, never fatal."""
    raw = os.getenv("MAX_RUN_LOCKS", "")
    if not raw:
        return 1024
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        _logger.warning("MAX_RUN_LOCKS=%r is not an integer — using 1024", raw)
        return 1024


_MAX_RUN_LOCKS = _max_run_locks()


class _RunLock(asyncio.Lock):
    """A per-run mutex that can say whether anyone still needs it.

    ``asyncio.Lock.locked()`` goes False the instant ``release()`` returns —
    while the coroutine that release just woke has NOT been scheduled yet and
    still holds a reference to this object. Dropping the lock in that window
    hands the next caller a DIFFERENT lock object, and two coroutines end up
    inside the same run's critical section.

    The window is not theoretical. ``OrchestrationWorker.run_forever`` does
    ``release`` → ``await queue.dequeue()`` → ``get_lock(next run)``, and
    ``dequeue`` is ``await asyncio.Queue.get()`` (kafka_queue.py:128), which
    does **not** yield to the loop when the queue is non-empty. Under backlog
    — the only condition in which the size backstop fires at all — those three
    steps are one uninterrupted step, and ``locked()`` reads False in the
    middle of it.

    ``in_use()`` counts waiters as well as the holder, using nothing but the
    public API. Subclassing keeps ``isinstance(x, asyncio.Lock)`` true, which
    the orchestration worker's ``EditNodeDataAction`` guard relies on.
    """

    def __init__(self) -> None:
        super().__init__()
        self._waiting = 0

    async def acquire(self) -> bool:
        self._waiting += 1
        try:
            return await super().acquire()
        finally:
            self._waiting -= 1

    def in_use(self) -> bool:
        """True while anyone holds this lock **or** is waiting to."""
        return self.locked() or self._waiting > 0


class RunScopeManager:
    """Per-run lock + scope + resolver-cache lifecycle.

    All preconditions match the pre-extraction engine semantics: when
    ``scope_builder`` or ``workflow_repo`` is missing the scope-building
    path silently no-ops, returning ``None`` tokens — same as before.
    """

    def __init__(
        self,
        *,
        run_repo: Any,
        workflow_repo: Any = None,
        workflow_snapshot_repo: Any = None,
        scope_builder: Optional[ResolutionScopeBuilder] = None,
        variable_resolver: Any = None,
        cache_registry: Any = None,
    ) -> None:
        self._run_repo = run_repo
        # Every cache that keyed itself by run_id and joined the registry gets
        # swept on a terminal run — see ``release_run``. Defaults to the
        # process-wide registry so components join at construction.
        self._cache_registry = (
            default_run_scoped_cache_registry()
            if cache_registry is None else cache_registry
        )
        # ``workflow_repo`` is kept as a presence sentinel so the gates
        # below match the pre-extraction engine exactly (scope was
        # historically skipped whenever the workflow repo was missing).
        self._workflow_repo = workflow_repo
        self._workflow_snapshot_repo = workflow_snapshot_repo
        self._scope_builder = scope_builder
        self._variable_resolver = variable_resolver
        # Ordered so the backstop can evict the least-recently-used first.
        self._run_locks: OrderedDict[str, _RunLock] = OrderedDict()
        default_cache_budget_registry().register(self, "run_locks")

    # ------------------------------------------------------------------
    # Locks
    # ------------------------------------------------------------------

    def get_lock(self, run_id: str) -> asyncio.Lock:
        """Return the per-run lock, creating it on first use."""
        lock = self._run_locks.get(run_id)
        if lock is None:
            lock = _RunLock()
            self._run_locks[run_id] = lock
            self._evict_unheld_locks()
        else:
            self._run_locks.move_to_end(run_id)
        return lock

    def release_lock(self, run_id: str) -> bool:
        """Drop the per-run lock once the run reaches a terminal state.

        Refuses to drop a lock that is currently IN USE — held by someone, or
        with a coroutine waiting on it. This matters: the COMPLETED/FAILED
        transition runs *inside* ``async with self._get_run_lock(run_id)``, so
        the old unconditional ``pop`` removed the lock while its own caller
        still owned it — after which the next caller would build a FRESH lock
        and could enter the critical section alongside anyone still waiting on
        the old object. A lock in use is left alone and reclaimed by
        :meth:`_evict_unheld_locks` once it is genuinely idle.

        Returns True when a lock was actually released.
        """
        lock = self._run_locks.get(run_id)
        if lock is None:
            return False
        if lock.in_use():
            _logger.debug(
                "run lock for %s is still in use — leaving it for the backstop",
                run_id,
            )
            return False
        del self._run_locks[run_id]
        return True

    def _evict_unheld_locks(self) -> None:
        """Bound ``_run_locks`` for runs that never reached a terminal state.

        The dict was unbounded: ``release_lock`` was called only on the
        COMPLETED/FAILED path, so every CANCELLED run — and every run that died
        without any transition — left one ``asyncio.Lock`` resident for the
        pod's lifetime. Terminal release is still the normal path; this is the
        floor under it.

        Only IDLE locks are evicted, oldest first. Evicting a lock someone
        still needs would trade a small leak for a correctness bug, which is
        never the right way round — and ``locked()`` alone is not enough to
        tell: see :class:`_RunLock` for the window it misses.
        """
        if len(self._run_locks) <= _MAX_RUN_LOCKS:
            return
        for run_id in list(self._run_locks):
            if len(self._run_locks) <= _MAX_RUN_LOCKS:
                return
            if not self._run_locks[run_id].in_use():
                del self._run_locks[run_id]

    def lock_count(self) -> int:
        """Resident per-run locks. Used by tests and the memory budget."""
        return len(self._run_locks)

    def budget_snapshot(self) -> dict:
        """Report into the SA-2256 memory budget (CacheBudgetRegistry)."""
        held = sum(1 for lock in self._run_locks.values() if lock.locked())
        # Reported separately from ``held``: a lock with waiters is contention,
        # which is what a rising number here means — same-run actions queueing
        # behind each other rather than running.
        in_use = sum(1 for lock in self._run_locks.values() if lock.in_use())
        return {
            "entries": len(self._run_locks),
            "maxsize": _MAX_RUN_LOCKS,
            "held": held,
            "in_use": in_use,
        }

    # ------------------------------------------------------------------
    # Contextvar scope
    # ------------------------------------------------------------------

    def enter(
        self,
        run_id: str,
        run: WorkflowRun | None = None,
    ) -> tuple[Any, Any]:
        """Install ``current_run_id`` and ``current_scope`` contextvars.

        Returns a (run_token, scope_token) pair; pass to :meth:`exit` in
        a ``finally`` block. Either token may be ``None`` if its
        contextvar was not set (no scope_builder, missing workflow,
        repo lookup failure).
        """
        run_token = set_current_run_id(run_id)
        scope_token: Any = None
        if self._scope_builder is not None and self._workflow_repo is not None:
            loaded = run if run is not None else self._run_repo.get_by_id(run_id)
            if loaded is not None:
                workflow = self._load_workflow(loaded)
                if workflow is not None:
                    scope = self.build_scope(loaded, workflow)
                    scope_token = set_current_scope(scope)
        return run_token, scope_token

    @staticmethod
    def exit(tokens: tuple[Any, Any]) -> None:
        run_token, scope_token = tokens
        if scope_token is not None:
            reset_current_scope(scope_token)
        reset_current_run_id(run_token)

    # ------------------------------------------------------------------
    # Scope building
    # ------------------------------------------------------------------

    def build_scope(
        self,
        run: WorkflowRun,
        workflow: Workflow,
        *,
        iteration_locals: Mapping[str, Any] | None = None,
    ) -> ResolutionScope:
        """Build the canonical scope chain for ``run`` via the builder.

        Resolves three pieces before delegating to
        :meth:`ResolutionScopeBuilder.build`:

        - the parent-run scope chain (for ``$parent`` / ``$root``)
        - the inherited user layer (root ``user_variables`` ⊕ root
          INPUT contributions) for sub-workflow runs
        - this run's own INPUT contributions (top-level scalars from
          completed INPUT-node submissions, exposed as ``$variable.X``)
        """
        assert self._scope_builder is not None  # guarded by caller
        parent_scope = self._build_parent_scope(run)
        root_layer, root_input_refs = self._resolve_root_user_variables(run)
        return self._scope_builder.build(
            run,
            workflow=workflow,
            parent_scope=parent_scope,
            iteration_locals=iteration_locals,
            root_user_variables=root_layer,
            inherited_input_refs=root_input_refs,
        )

    def _build_parent_scope(self, run: WorkflowRun) -> ResolutionScope | None:
        """Lazily build the parent run's scope so ``$parent`` / ``$root``
        expressions work for sub-workflow runs.

        Returns ``None`` for top-level runs. Recursive — the parent's
        own ``ParentRunFrame`` carries its grandparent scope, which is
        what makes ``$root`` walk to the topmost ancestor without the
        builder needing to know how deep the nesting goes. Bounded by
        the orchestration-engine recursion limit on ``run.depth_level``.
        """
        if self._scope_builder is None or self._workflow_repo is None:
            return None
        if not run.parent_run_id:
            return None
        parent_run = self._run_repo.get_by_id(run.parent_run_id)
        if parent_run is None:
            return None
        parent_workflow = self._load_workflow(parent_run)
        if parent_workflow is None:
            return None
        grandparent = self._build_parent_scope(parent_run)
        root_layer, root_input_refs = self._resolve_root_user_variables(parent_run)
        return self._scope_builder.build(
            parent_run,
            workflow=parent_workflow,
            parent_scope=grandparent,
            root_user_variables=root_layer,
            inherited_input_refs=root_input_refs,
        )

    def _resolve_root_user_variables(
        self,
        run: WorkflowRun,
    ) -> tuple[Mapping[str, Any] | None, tuple[tuple[str, Any], ...]]:
        """Return the ROOT run's inherited user layer for sub-workflows.

        For sub-workflow runs returns ``(root.user_variables, root_input_refs)``:
        the root's in-memory user-variable overrides, plus the ``(node_id,
        file_ref)`` pairs for the root's completed INPUT nodes. Both layer
        above the child workflow's ``variables`` defaults inside ``$variable.X``;
        the root INPUT scalars are fetched lazily by the child's
        :class:`UserVariablesFrame` (no-``extracted`` OData model). The child's
        own INPUT contributions merge on top (last layer wins).

        Top-level runs (``root_run_id == id`` or empty) return ``(None, ())``,
        which signals :meth:`ResolutionScopeBuilder.build` to read
        ``run.user_variables`` directly — own-only path, no inheritance.
        """
        if not run.root_run_id or run.root_run_id == run.id:
            return None, ()
        root_run = self._run_repo.get_by_id(run.root_run_id)
        if root_run is None:
            return None, ()
        layer: dict[str, Any] = dict(getattr(root_run, "user_variables", {}) or {})
        root_input_refs: tuple[tuple[str, Any], ...] = ()
        if self._workflow_snapshot_repo is not None:
            root_workflow = self._load_workflow(root_run)
            if root_workflow is not None:
                root_input_refs = ResolutionScopeBuilder._discover_input_refs(
                    root_run, root_workflow,
                )
        return layer, root_input_refs

    def _load_workflow(self, run: WorkflowRun) -> Workflow | None:
        if self._workflow_snapshot_repo is None:
            return None
        try:
            return _load_workflow_for_run(run, self._workflow_snapshot_repo)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Resolver cache cleanup
    # ------------------------------------------------------------------

    def release_run(self, run_id: str) -> int:
        """Release every registered per-run cache for a terminal run.

        This is the call terminal transitions should make. It covers two
        discovery mechanisms:

        * the legacy duck-typed scan over the variable resolver's private
          strategy list (``drop_resolver_cache``), kept so nothing that relies
          on it regresses, and
        * the explicit ``RunScopedCacheRegistry``, which is how any other
          component — starting with the projector view store — gets told.

        The scan alone was the bug: it reached exactly one collaborator and
        was silent about everything else, so three per-run dicts in
        ``CachedProjectorRunContextViewStore`` were never released at all
        (SA-2258).

        Returns the number of registry members swept. Deliberately does NOT
        touch ``_run_locks`` — lock lifetime is a concurrency concern with its
        own call sites, not a cache concern.
        """
        self.drop_resolver_cache(run_id)
        # SA-2274 — the per-run lock is per-run state too. Releasing it here
        # covers the CANCELLED path, which reaches this method (via
        # ``drop_run_resolver_cache``) but never called ``release_lock``, so
        # every cancelled run used to leave one ``asyncio.Lock`` behind.
        # A lock still HELD is skipped and reclaimed by the backstop.
        self.release_lock(run_id)
        return self._cache_registry.release_run(run_id)

    def drop_resolver_cache(self, run_id: str) -> None:
        """Release all per-run resolver caches for a completed run.

        Iterates the strategy chain on the injected variable resolver
        and calls ``drop_run_cache`` on any strategy that supports it
        (today only ``FileBackedResolverStrategy``). Called from
        terminal transitions so long-lived deployments don't leak
        memory from completed runs.
        """
        if not self._variable_resolver:
            return
        strategies = getattr(self._variable_resolver, "_strategies", None)
        if not strategies:
            return
        for strategy in strategies:
            drop = getattr(strategy, "drop_run_cache", None)
            if callable(drop):
                try:
                    drop(run_id)
                except Exception:
                    _logger.debug(
                        "drop_run_cache failed for strategy %s run_id=%s",
                        type(strategy).__name__, run_id,
                        exc_info=True,
                    )
