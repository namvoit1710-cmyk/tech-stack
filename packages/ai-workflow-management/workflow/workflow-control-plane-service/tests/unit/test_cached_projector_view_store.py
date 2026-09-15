"""Unit tests for ``CachedProjectorRunContextViewStore`` — Phase 4c.2.

Verifies the cache lifecycle contract:

1. **Cold path** — first ``get(run_id)`` rebuilds from the full log.
2. **Warm path, no new events** — subsequent ``get(run_id)`` hits
   ``list_by_run_since`` and folds zero events (hot path).
3. **Warm path with Δ new events** — intervening appends are folded
   incrementally into the cached state.
4. **Invalidate** — explicit cache clear forces the next ``get()``
   to rebuild.
5. **Missing run** — returns ``None`` for runs with no logged events.
6. **list_by_run_since conformance** on both backends — the new repo
   method returns only events with ``sequence > min_sequence``.
"""

from __future__ import annotations

from app.layer1_domain.events import NodeCompleted, NodeCancelled
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.sqlite.sqlite_event_log_repo import (
    SqliteEventLogRepository,
)
from app.layer4_frameworks.run_context.cached_projector_view_store import (
    CachedProjectorRunContextViewStore,
)


def _envelope(run_id: str, event_id: str, event_type: str = "NodeCompleted", **payload):
    return EventLogEnvelope(
        event_id=event_id,
        run_id=run_id,
        topic=event_type.lower(),
        event_type=event_type,
        timestamp="2026-04-11T00:00:00Z",
        payload={**payload, "run_id": run_id},
    )


# ──────────────── list_by_run_since on both backends ───────────────


class TestListByRunSinceMemory:
    def _filled_repo(self):
        repo = MemoryEventLogRepository()
        repo.append(_envelope("run-1", "e1", node_id="n1"))
        repo.append(_envelope("run-1", "e2", node_id="n2"))
        repo.append(_envelope("run-1", "e3", node_id="n3"))
        repo.append(_envelope("run-2", "eX", node_id="nX"))
        return repo

    def test_returns_only_events_after_min_sequence(self):
        repo = self._filled_repo()
        after_one = repo.list_by_run_since("run-1", 1)
        assert [e.event_id for e in after_one] == ["e2", "e3"]

    def test_min_sequence_at_or_above_max_returns_empty(self):
        repo = self._filled_repo()
        assert repo.list_by_run_since("run-1", 3) == []
        assert repo.list_by_run_since("run-1", 99) == []

    def test_min_sequence_zero_returns_everything(self):
        repo = self._filled_repo()
        all_events = repo.list_by_run_since("run-1", 0)
        assert [e.event_id for e in all_events] == ["e1", "e2", "e3"]

    def test_scoped_to_single_run(self):
        repo = self._filled_repo()
        # run-2 only has one event and it hasn't crossed sequence 0
        assert [e.event_id for e in repo.list_by_run_since("run-2", 0)] == ["eX"]
        # Filtering run-1 doesn't leak run-2's events
        assert all(
            e.run_id == "run-1"
            for e in repo.list_by_run_since("run-1", 0)
        )


class TestListByRunSinceSqlite:
    """Mirror of TestListByRunSinceMemory, run against the SQLite
    backend. Any drift in semantics between backends is a migration
    hazard."""

    def _filled_repo(self):
        repo = SqliteEventLogRepository(":memory:")
        repo.append(_envelope("run-1", "e1", node_id="n1"))
        repo.append(_envelope("run-1", "e2", node_id="n2"))
        repo.append(_envelope("run-1", "e3", node_id="n3"))
        repo.append(_envelope("run-2", "eX", node_id="nX"))
        return repo

    def test_returns_only_events_after_min_sequence(self):
        repo = self._filled_repo()
        try:
            after_one = repo.list_by_run_since("run-1", 1)
            assert [e.event_id for e in after_one] == ["e2", "e3"]
        finally:
            repo.close()

    def test_min_sequence_at_or_above_max_returns_empty(self):
        repo = self._filled_repo()
        try:
            assert repo.list_by_run_since("run-1", 3) == []
            assert repo.list_by_run_since("run-1", 99) == []
        finally:
            repo.close()

    def test_min_sequence_zero_returns_everything(self):
        repo = self._filled_repo()
        try:
            all_events = repo.list_by_run_since("run-1", 0)
            assert [e.event_id for e in all_events] == ["e1", "e2", "e3"]
        finally:
            repo.close()


# ───────────────── cached view store lifecycle ────────────────────


class TestCachedStoreLifecycle:
    def _primed_repo(self) -> MemoryEventLogRepository:
        repo = MemoryEventLogRepository()
        repo.append(_envelope("run-1", "rs", "RunStatusChanged", status="started", workflow_id="wf-1"))
        repo.append(_envelope("run-1", "tc1", "NodeCompleted", node_id="n1"))
        repo.append(_envelope("run-1", "tc2", "NodeCompleted", node_id="n2"))
        return repo

    def test_cold_get_rebuilds_and_caches(self):
        repo = self._primed_repo()
        store = CachedProjectorRunContextViewStore(repo)
        assert store.cache_size() == 0

        view = store.get("run-1")
        assert view is not None
        assert view.status == "RUNNING"
        assert view.workflow_id == "wf-1"
        assert view.completed_nodes == frozenset({"n1", "n2"})
        assert store.cache_size() == 1

    def test_warm_get_returns_cached_without_rebuild(self):
        """After the first get(), subsequent gets should NOT re-fold
        every event. Verified by wrapping list_by_run in a call counter.
        """
        repo = self._primed_repo()

        call_log: list[str] = []
        original_list_by_run = repo.list_by_run
        original_list_by_run_since = repo.list_by_run_since

        def counting_list_by_run(run_id):
            call_log.append(f"list_by_run:{run_id}")
            return original_list_by_run(run_id)

        def counting_list_by_run_since(run_id, min_sequence):
            call_log.append(f"list_by_run_since:{run_id}:{min_sequence}")
            return original_list_by_run_since(run_id, min_sequence)

        repo.list_by_run = counting_list_by_run  # type: ignore[method-assign]
        repo.list_by_run_since = counting_list_by_run_since  # type: ignore[method-assign]

        store = CachedProjectorRunContextViewStore(repo)

        # Cold — expect one list_by_run call for full rebuild
        view_1 = store.get("run-1")
        assert view_1 is not None
        assert "list_by_run:run-1" in call_log

        # Warm with no new events — expect list_by_run_since (returns empty),
        # NOT another list_by_run full scan.
        call_log.clear()
        view_2 = store.get("run-1")
        assert view_2 is not None
        # The warm path must NOT re-fetch the full log.
        assert not any(c.startswith("list_by_run:") for c in call_log), (
            f"warm path should not call list_by_run; got {call_log}"
        )
        # It SHOULD call list_by_run_since with the cached sequence (3).
        assert any(
            c.startswith("list_by_run_since:run-1:") for c in call_log
        ), f"expected list_by_run_since call; got {call_log}"

        # State equivalence
        assert view_2.completed_nodes == view_1.completed_nodes
        assert view_2.status == view_1.status

    def test_incremental_update_folds_new_events(self):
        repo = self._primed_repo()
        store = CachedProjectorRunContextViewStore(repo)

        # Prime cache
        view_before = store.get("run-1")
        assert view_before is not None
        assert view_before.completed_nodes == frozenset({"n1", "n2"})
        assert view_before.status == "RUNNING"

        # Append new events (simulating engine publish)
        repo.append(_envelope("run-1", "tc3", "NodeCompleted", node_id="n3"))
        repo.append(_envelope("run-1", "ts1", "NodeCancelled", node_id="n_err",
                               reason="branch_not_taken"))
        repo.append(_envelope("run-1", "rc", "RunStatusChanged", status="completed"))

        # Warm get: should fold in the 3 new events
        view_after = store.get("run-1")
        assert view_after is not None
        assert view_after.completed_nodes == frozenset({"n1", "n2", "n3"})
        assert view_after.skipped_nodes == frozenset({"n_err"})
        assert view_after.status == "COMPLETED"

    def test_invalidate_forces_rebuild(self):
        repo = self._primed_repo()
        store = CachedProjectorRunContextViewStore(repo)

        store.get("run-1")
        assert store.cache_size() == 1

        store.invalidate("run-1")
        assert store.cache_size() == 0

        # Next get rebuilds from scratch
        view = store.get("run-1")
        assert view is not None
        assert view.completed_nodes == frozenset({"n1", "n2"})
        assert store.cache_size() == 1

    def test_invalidate_all_clears_every_entry(self):
        repo = self._primed_repo()
        repo.append(_envelope("run-2", "rs2", "RunStatusChanged", status="started", workflow_id="wf-2"))
        store = CachedProjectorRunContextViewStore(repo)

        store.get("run-1")
        store.get("run-2")
        assert store.cache_size() == 2

        store.invalidate_all()
        assert store.cache_size() == 0

    def test_missing_run_returns_none(self):
        repo = MemoryEventLogRepository()  # empty
        store = CachedProjectorRunContextViewStore(repo)
        assert store.get("never-started") is None
        # And does not populate the cache
        assert store.cache_size() == 0

    def test_get_by_root_id_raises(self):
        import pytest
        repo = MemoryEventLogRepository()
        store = CachedProjectorRunContextViewStore(repo)
        with pytest.raises(NotImplementedError, match="get_by_root_id"):
            store.get_by_root_id("root-1")

    def test_variables_round_trip_via_node_output_produced(self):
        """Phase 5.1: variables flow through NodeOutputProduced. The
        cached store must fold these events correctly on both cold
        and warm paths."""
        repo = MemoryEventLogRepository()
        repo.append(_envelope("run-1", "rs", "RunStatusChanged", status="started", workflow_id="wf-1"))
        repo.append(
            _envelope(
                "run-1", "nop1", "NodeOutputProduced",
                node_id="n1",
                state_updates={"n1": {"value": 42}},
            )
        )
        store = CachedProjectorRunContextViewStore(repo)

        view_cold = store.get("run-1")
        assert view_cold is not None
        assert view_cold.variables == {"n1": {"value": 42}}

        # Add more writes and verify warm-path merges
        repo.append(
            _envelope(
                "run-1", "nop2", "NodeOutputProduced",
                node_id="n2",
                state_updates={"n2": {"value": "x"}},
            )
        )
        view_warm = store.get("run-1")
        assert view_warm is not None
        assert view_warm.variables == {
            "n1": {"value": 42},
            "n2": {"value": "x"},
        }


# ────────────── Phase 4c.3: topology + get_by_root_id fallback ───────────────


class TestProjectedViewDagInfoFallback:
    """Phase 4c.3: ``ProjectedView`` gains a ``workflow_repo`` fallback
    so ``topology`` and ``entry_nodes`` return real values instead of
    empty defaults when a workflow repo is wired.
    """

    def _build_workflow_and_repo(self):
        from app.layer1_domain.entities.workflow import (
            Workflow, WorkflowNode, WorkflowEdge,
        )
        from app.layer1_domain.value_objects.node_type import NodeType
        from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
            MemoryWorkflowRepository,
        )

        wf = Workflow(
            id="wf-phase4c3",
            name="phase4c3",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="trigger", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="compute-1", name="c",
                    node_type=NodeType.COMPUTE,
                    config={"expression": "1"},
                ),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="trigger-1", target_node_id="compute-1"),
            ],
        )
        wf_repo = MemoryWorkflowRepository()
        wf_repo.save(wf)
        return wf, wf_repo

    def test_without_workflow_repo_returns_empty_topology(self):
        """Preserves the Phase 4c.1 contract for callers that don't
        opt into the fallback — no behavior change."""
        from app.layer1_domain.entities.workflow_run import GraphTopology
        from app.layer2_application.projection import ProjectedRunState
        from app.layer4_frameworks.run_context.projector_view_store import ProjectedView

        state = ProjectedRunState(run_id="run-1", workflow_id="wf-phase4c3")
        view = ProjectedView(state)  # No workflow_repo

        assert view.topology.adjacency == {}
        assert view.topology.entry_nodes == []
        assert view.entry_nodes == ()

    def test_with_workflow_repo_returns_real_topology(self):
        from app.layer2_application.projection import ProjectedRunState
        from app.layer4_frameworks.run_context.projector_view_store import ProjectedView

        wf, wf_repo = self._build_workflow_and_repo()
        state = ProjectedRunState(run_id="run-1", workflow_id=wf.id)
        view = ProjectedView(state, workflow_repo=wf_repo)

        # Real GraphTopology populated via TopologyBuilder.prepare
        assert view.topology.entry_nodes == ["trigger-1"]
        assert view.topology.adjacency == {
            "trigger-1": ["compute-1"], "compute-1": [],
        }
        assert view.topology.predecessors == {
            "trigger-1": [], "compute-1": ["trigger-1"],
        }
        assert view.entry_nodes == ("trigger-1",)

    def test_topology_is_memoized_per_view_instance(self):
        """Multiple reads of ``topology`` should trigger a single
        workflow_repo lookup — per-instance memoization."""
        from app.layer2_application.projection import ProjectedRunState
        from app.layer4_frameworks.run_context.projector_view_store import ProjectedView

        wf, wf_repo = self._build_workflow_and_repo()

        # Wrap the workflow_repo to count get_by_id calls
        call_count = 0
        original_get = wf_repo.get_by_id

        def counting_get(workflow_id):
            nonlocal call_count
            call_count += 1
            return original_get(workflow_id)

        wf_repo.get_by_id = counting_get  # type: ignore[method-assign]

        state = ProjectedRunState(run_id="run-1", workflow_id=wf.id)
        view = ProjectedView(state, workflow_repo=wf_repo)

        # 10 reads — should be exactly 1 underlying call
        for _ in range(10):
            _ = view.topology
            _ = view.entry_nodes
        assert call_count == 1, (
            f"expected 1 workflow_repo.get_by_id call, got {call_count} — "
            f"topology memoization is broken"
        )

    def test_missing_workflow_id_returns_empty_topology(self):
        """If the projected state has no workflow_id (e.g., events not
        yet replayed), the fallback must not blow up — returns empty."""
        from app.layer2_application.projection import ProjectedRunState
        from app.layer4_frameworks.run_context.projector_view_store import ProjectedView

        wf, wf_repo = self._build_workflow_and_repo()
        state = ProjectedRunState(run_id="run-1", workflow_id="")  # empty
        view = ProjectedView(state, workflow_repo=wf_repo)

        assert view.topology.entry_nodes == []
        assert view.entry_nodes == ()

    def test_unknown_workflow_id_returns_empty_topology(self):
        """If the workflow_repo has no matching entry, the fallback
        returns empty GraphTopology rather than raising. This happens if the
        workflow was deleted between run start and projection rebuild."""
        from app.layer2_application.projection import ProjectedRunState
        from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
            MemoryWorkflowRepository,
        )
        from app.layer4_frameworks.run_context.projector_view_store import ProjectedView

        wf_repo = MemoryWorkflowRepository()  # empty
        state = ProjectedRunState(run_id="run-1", workflow_id="wf-deleted")
        view = ProjectedView(state, workflow_repo=wf_repo)

        assert view.topology.entry_nodes == []
        assert view.entry_nodes == ()


class TestCachedStoreGetByRootId:
    """Phase 4c.3: ``get_by_root_id`` now works when a ``run_repo`` is
    wired. Delegates to ``run_repo.get_by_root_id`` for cross-run
    discovery, then replays each run's event log via ``self.get``.
    """

    def _primed_repos(self):
        """Build a memory event log + memory run repo + matching workflow
        repo with a parent run containing one child run."""
        from app.layer1_domain.entities.workflow import (
            Workflow, WorkflowNode, WorkflowEdge,
        )
        from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
        from app.layer1_domain.value_objects.node_type import NodeType
        from app.layer1_domain.value_objects.run_status import RunStatus
        from app.layer4_frameworks.persistence.memory.memory_run_repo import (
            MemoryRunRepository,
        )
        from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
            MemoryWorkflowRepository,
        )

        event_log_repo = MemoryEventLogRepository()
        run_repo = MemoryRunRepository()
        wf_repo = MemoryWorkflowRepository()

        # Parent workflow with a WORKFLOW node
        parent_wf = Workflow(
            id="wf-parent",
            name="parent",
            version="1",
            nodes=[
                WorkflowNode(id="trigger-1", name="t", node_type=NodeType.TRIGGER),
            ],
        )
        child_wf = Workflow(
            id="wf-child",
            name="child",
            version="1",
            nodes=[
                WorkflowNode(id="child-trigger", name="ct", node_type=NodeType.TRIGGER),
            ],
        )
        wf_repo.save(parent_wf)
        wf_repo.save(child_wf)

        # Register parent and child runs in the run repo with matching
        # root_run_id so get_by_root_id finds both.
        parent_run = WorkflowRun(
            id="run-parent", workflow_id="wf-parent",
            status=RunStatus.COMPLETED,
            root_run_id="run-parent",
            context=RunContext(),
        )
        child_run = WorkflowRun(
            id="run-child", workflow_id="wf-child",
            status=RunStatus.COMPLETED,
            parent_run_id="run-parent", root_run_id="run-parent",
            context=RunContext(),
        )
        run_repo.save(parent_run)
        run_repo.save(child_run)

        # Seed the event log with minimal state so get(run_id) returns non-None
        event_log_repo.append(_envelope("run-parent", "rs1", "RunStatusChanged", status="started", workflow_id="wf-parent"))
        event_log_repo.append(_envelope("run-parent", "rc1", "RunStatusChanged", status="completed"))
        event_log_repo.append(_envelope("run-child", "rs2", "RunStatusChanged", status="started", workflow_id="wf-child"))
        event_log_repo.append(_envelope("run-child", "rc2", "RunStatusChanged", status="completed"))

        return event_log_repo, run_repo, wf_repo

    def test_without_run_repo_raises_not_implemented(self):
        """Regression guard: stores constructed without a run_repo
        preserve the Phase 4c.1/4c.2 contract."""
        import pytest
        event_log_repo = MemoryEventLogRepository()
        store = CachedProjectorRunContextViewStore(event_log_repo)
        with pytest.raises(NotImplementedError, match="run_repo"):
            store.get_by_root_id("root-1")

    def test_with_run_repo_returns_views_for_all_descendants(self):
        event_log_repo, run_repo, wf_repo = self._primed_repos()
        store = CachedProjectorRunContextViewStore(
            event_log_repo, workflow_repo=wf_repo, run_repo=run_repo,
        )

        views = store.get_by_root_id("run-parent")
        run_ids = sorted(v.run_id for v in views)
        assert run_ids == ["run-child", "run-parent"]

        # Each view is a real projector view with event-sourced state
        parent_view = next(v for v in views if v.run_id == "run-parent")
        child_view = next(v for v in views if v.run_id == "run-child")
        assert parent_view.workflow_id == "wf-parent"
        assert parent_view.status == "COMPLETED"
        assert child_view.workflow_id == "wf-child"
        assert child_view.status == "COMPLETED"

    def test_get_by_root_id_falls_back_to_aggregate_for_runs_without_events(self):
        """Runs the run_repo returns but that have no events in the
        log fall back to the aggregate via ``_aggregate_fallback``.
        This covers the pre-first-emission window and test fixtures
        that populate a ``MemoryRunRepository`` directly without an
        event loop — both need a valid view, not ``None``.

        Before the fallback landed, this case returned ``None`` from
        ``get(run_id)`` and the caller silently skipped. Post-fallback,
        the aggregate wins."""
        event_log_repo, run_repo, wf_repo = self._primed_repos()
        # Clear the child's events — run still in run_repo but no events
        event_log_repo.clear("run-child")

        store = CachedProjectorRunContextViewStore(
            event_log_repo, workflow_repo=wf_repo, run_repo=run_repo,
        )
        views = store.get_by_root_id("run-parent")
        run_ids = sorted(v.run_id for v in views)
        assert run_ids == ["run-child", "run-parent"]
        child_view = next(v for v in views if v.run_id == "run-child")
        # Aggregate-projected view: status comes from the run aggregate,
        # not the (empty) event log.
        assert child_view.workflow_id == "wf-child"


class TestProjectorStoreDagInfoFallback:
    """Mirror of TestProjectedViewDagInfoFallback against the
    **non-cached** ``ProjectorRunContextViewStore`` so the two stores
    stay in sync on the fallback contract."""

    def test_direct_store_returns_real_topology_via_workflow_repo(self):
        from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
        from app.layer1_domain.value_objects.node_type import NodeType
        from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
            MemoryWorkflowRepository,
        )
        from app.layer4_frameworks.run_context.projector_view_store import (
            ProjectorRunContextViewStore,
        )

        wf = Workflow(
            id="wf-1", name="w", version="1",
            nodes=[
                WorkflowNode(id="t1", name="t", node_type=NodeType.TRIGGER),
                WorkflowNode(id="c1", name="c", node_type=NodeType.COMPUTE, config={"expression": "1"}),
            ],
            edges=[WorkflowEdge(id="e1", source_node_id="t1", target_node_id="c1")],
        )
        wf_repo = MemoryWorkflowRepository()
        wf_repo.save(wf)

        event_log_repo = MemoryEventLogRepository()
        event_log_repo.append(_envelope("run-1", "rs", "RunStatusChanged", status="started", workflow_id="wf-1"))

        from app.layer4_frameworks.run_context.projector_view_store import (
            ProjectorRunContextViewStore,
        )
        store = ProjectorRunContextViewStore(event_log_repo, workflow_repo=wf_repo)
        view = store.get("run-1")
        assert view is not None
        assert view.entry_nodes == ("t1",)
        assert view.topology.adjacency == {"t1": ["c1"], "c1": []}


# ────────────── read-path accounting: cold vs warm (SA-2368 follow-on) ──────────────
#
# Measured on tenant-1 2026-08-13: ONE read of ONE existing run cost a 185 MiB
# transient RSS peak with run_locks +0 and orchestration hops +0 -- no run
# started -- and with full_materialize/arrow_read/native_split all flat, so no
# file work either. The whole cost was replay plus variable resolution.
#
# The bucket exported occupancy only, so a warm read served from `_cache` and a
# full event-log replay were indistinguishable on the dashboard. "The read path
# is expensive" could not be turned into a threshold. These tests defend the
# numbers that make it one.


class _CountingMonitor:
    def __init__(self):
        self.hist: list[tuple] = []

    def histogram(self, metric, value, tags=None):
        self.hist.append((metric, value, tags))

    def increment(self, metric, tags=None, amount=1):
        pass

    def track(self, metric, value):
        pass


class TestReadPathAccounting:
    def _primed_repo(self) -> MemoryEventLogRepository:
        repo = MemoryEventLogRepository()
        repo.append(_envelope("run-1", "rs", "RunStatusChanged",
                              status="started", workflow_id="wf-1"))
        repo.append(_envelope("run-1", "tc1", "NodeCompleted", node_id="n1"))
        return repo

    def test_the_counters_exist_at_zero_before_any_read(self):
        """A panel that reads "No data" until the first cold replay is a panel
        nobody can alert on -- and the first cold replay is exactly the event
        worth catching."""
        store = CachedProjectorRunContextViewStore(MemoryEventLogRepository())
        snap = store.budget_snapshot()
        for outcome in ("warm", "warm_delta", "cold_snapshot", "cold_replay",
                        "aggregate_fallback", "missing", "error"):
            assert snap[f"loads_{outcome}"] == 0
        assert snap["hits"] == 0
        assert snap["misses"] == 0
        assert snap["envelopes_replayed"] == 0

    def test_a_cold_read_is_counted_as_a_full_replay(self):
        store = CachedProjectorRunContextViewStore(self._primed_repo())
        store.get("run-1")

        snap = store.budget_snapshot()
        assert snap["loads_cold_replay"] == 1
        assert snap["misses"] == 1
        assert snap["hits"] == 0
        # and it says how much work it was, which is the whole point
        assert snap["envelopes_replayed"] == 2

    def test_a_warm_read_is_counted_separately_and_folds_nothing(self):
        store = CachedProjectorRunContextViewStore(self._primed_repo())
        store.get("run-1")
        store.get("run-1")

        snap = store.budget_snapshot()
        assert snap["loads_cold_replay"] == 1
        assert snap["loads_warm"] == 1
        assert snap["hits"] == 1
        assert snap["misses"] == 1
        assert snap["envelopes_replayed"] == 2  # unchanged by the warm read

    def test_a_warm_read_with_new_events_reports_the_delta_it_folded(self):
        repo = self._primed_repo()
        store = CachedProjectorRunContextViewStore(repo)
        store.get("run-1")
        repo.append(_envelope("run-1", "tc2", "NodeCompleted", node_id="n2"))
        repo.append(_envelope("run-1", "tc3", "NodeCompleted", node_id="n3"))
        store.get("run-1")

        snap = store.budget_snapshot()
        assert snap["loads_warm_delta"] == 1
        assert snap["loads_warm"] == 0
        assert snap["hits"] == 1
        assert snap["envelopes_replayed"] == 4  # 2 cold + 2 folded

    def test_a_missing_run_is_not_counted_as_a_cache_miss(self):
        """A miss means "we had to rebuild". Asking for a run that does not
        exist rebuilt nothing, and folding it into misses would inflate the
        very ratio this exists to measure."""
        store = CachedProjectorRunContextViewStore(MemoryEventLogRepository())
        assert store.get("nope") is None

        snap = store.budget_snapshot()
        assert snap["loads_missing"] == 1
        assert snap["misses"] == 0
        assert snap["hits"] == 0

    def test_a_read_that_raises_is_recorded_as_an_error_not_a_miss(self):
        class _Exploding(MemoryEventLogRepository):
            def list_by_run(self, run_id):
                raise RuntimeError("event log unavailable")

        store = CachedProjectorRunContextViewStore(_Exploding())
        try:
            store.get("run-1")
        except RuntimeError:
            pass
        else:  # pragma: no cover
            raise AssertionError("the error should propagate to the caller")

        snap = store.budget_snapshot()
        assert snap["loads_error"] == 1
        assert snap["misses"] == 0

    def test_every_read_is_timed_under_the_same_label_key(self):
        """prometheus_client freezes a family's label set on first observation
        and drops mismatched samples SILENTLY, so an occasionally-omitted tag
        loses data with no error anywhere."""
        monitor = _CountingMonitor()
        repo = self._primed_repo()
        store = CachedProjectorRunContextViewStore(repo, monitor=monitor)
        store.get("run-1")          # cold_replay
        store.get("run-1")          # warm
        store.get("does-not-exist")  # missing

        assert len(monitor.hist) == 3
        assert {m for m, _, _ in monitor.hist} == {"projector_view_load_seconds"}
        assert {frozenset(t.keys()) for _, _, t in monitor.hist} == {
            frozenset({"outcome"})
        }
        assert [t["outcome"] for _, _, t in monitor.hist] == [
            "cold_replay", "warm", "missing",
        ]
        assert all(v >= 0 for _, v, _ in monitor.hist)

    def test_a_broken_monitor_cannot_break_a_read(self):
        class _BadMonitor(_CountingMonitor):
            def histogram(self, metric, value, tags=None):
                raise RuntimeError("metrics backend is down")

        store = CachedProjectorRunContextViewStore(
            self._primed_repo(), monitor=_BadMonitor())
        assert store.get("run-1") is not None
        assert store.budget_snapshot()["loads_cold_replay"] == 1

    def test_the_counters_survive_alongside_the_occupancy_fields(self):
        """budget_snapshot feeds wcp_cache_<field>{bucket=...}; the existing
        fields must keep their names or the Resources row goes blank."""
        store = CachedProjectorRunContextViewStore(self._primed_repo())
        store.get("run-1")
        snap = store.budget_snapshot()
        for field in ("entries", "snap_watermark", "snap_history", "lru",
                      "max_runs", "evictions_terminal", "evictions_overflow"):
            assert field in snap, field
        assert snap["entries"] == 1
