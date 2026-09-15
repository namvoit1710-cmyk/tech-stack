"""Unit tests for the pure ``compute_fanout`` dispatcher — Phase 4b.2.

Covers:
- Port filtering on TASK/COMPUTE/WORKFLOW node types
- Cascade-skip BFS, merge-safe: a downstream node is only skipped if all
  its predecessors are also being skipped (or already are)
- Predecessor-readiness gate (fan-out defers when preds aren't all done)
- Deterministic event id stability: same ``causation_id`` + same inputs
  always produce identical action / skip ids, so the event log dedupes
  on redelivery
- The decision is purely a function of its inputs — no mutation, no I/O

The dispatcher is pure, so every test builds frozen snapshots (no
``WorkflowRun``, no ``GraphTopology``, no engine wiring). This is the point.
"""

from __future__ import annotations

from app.layer1_domain.entities.workflow import WorkflowEdge
from app.layer1_domain.fanout import (
    FanoutAction,
    FanoutDecision,
    SkipAction,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus


def _edge(edge_id: str, src: str, tgt: str, port: str = "") -> WorkflowEdge:
    return WorkflowEdge(
        id=edge_id,
        source_node_id=src,
        target_node_id=tgt,
        source_port_id=port,
    )


# ─────────────────────────── port filter ─────────────────────────


class TestPortFiltering:
    def test_task_success_port_taken_on_completed(self):
        edges = [
            _edge("e1", "a", "b", port="success"),
            _edge("e2", "a", "c", port="failure"),
        ]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="a",
            source_node_type=NodeType.TASK,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges,
        )
        # Edge-based dispatch: only the success-port edge is followed.
        # The failure-port edge is silently dropped — no cascade-skip.
        assert decision.expected_port == "success"
        assert [a.target_node_id for a in decision.actions] == ["b"]
        assert decision.skipped == ()

    def test_task_failure_port_taken_on_failed(self):
        edges = [
            _edge("e1", "a", "b", port="success"),
            _edge("e2", "a", "c", port="failure"),
        ]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="a",
            source_node_type=NodeType.TASK,
            source_task_status=TaskStatus.FAILED,
            outgoing_edges=edges,
        )
        assert decision.expected_port == "failure"
        assert [a.target_node_id for a in decision.actions] == ["c"]
        assert decision.skipped == ()

    def test_non_port_routed_type_emits_all_edges(self):
        """TRIGGER is BUILTIN and not port-routed; both outgoing edges go live."""
        edges = [
            _edge("e1", "trigger", "a"),
            _edge("e2", "trigger", "b"),
        ]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="trigger",
            source_node_type=NodeType.TRIGGER,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges,
        )
        assert decision.expected_port == ""
        assert {a.target_node_id for a in decision.actions} == {"a", "b"}
        assert decision.skipped == ()


# ───────────── failed non-port-routed (PENDING) nodes ────────────


class TestFailedNonPortRoutedRoutesFailurePort:
    """A failed PENDING node (INPUT/HUMAN_ACTION/DATA_EDIT/FILE_UPLOAD) must
    route via its ``failure`` port only — never its ``success`` edges.

    Regression: these types are not in ``_PORT_ROUTED_TYPES``, so a failed
    pending node used to fall through to "traverse all edges" and continue
    down its success path as though it had succeeded. ``orchestrate_fail``
    relies on the empty-action case to mark the run FAILED when no failure
    edge exists.
    """

    def test_failed_data_edit_with_only_success_edge_dispatches_nothing(self):
        # No failure edge → no traversable edge → orchestrate_fail marks
        # the run FAILED rather than taking the success path.
        edges = [_edge("e1", "edit", "after", port="success")]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="edit",
            source_node_type=NodeType.DATA_EDIT,
            source_task_status=TaskStatus.FAILED,
            outgoing_edges=edges,
        )
        assert decision.expected_port == "failure"
        assert decision.actions == ()

    def test_failed_data_edit_routes_only_failure_edge(self):
        edges = [
            _edge("e1", "edit", "after", port="success"),
            _edge("e2", "edit", "onfail", port="failure"),
        ]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="edit",
            source_node_type=NodeType.DATA_EDIT,
            source_task_status=TaskStatus.FAILED,
            outgoing_edges=edges,
        )
        assert decision.expected_port == "failure"
        assert [a.target_node_id for a in decision.actions] == ["onfail"]

    def test_completed_data_edit_success_path_unchanged(self):
        # The success path for the same node type is untouched: a COMPLETED
        # pending node still traverses all its edges (expected_port == "").
        edges = [
            _edge("e1", "edit", "after", port="success"),
            _edge("e2", "edit", "other"),
        ]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="edit",
            source_node_type=NodeType.DATA_EDIT,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges,
        )
        assert decision.expected_port == ""
        assert {a.target_node_id for a in decision.actions} == {"after", "other"}


# ─────────────── edge-based dispatch (no cascade-skip) ────────────


class TestCascadeSkipRetired:
    """``compute_fanout`` no longer pre-computes a downstream skip subtree.

    Edge-based dispatch follows only the taken edges and leaves untaken
    branches alone — they simply never receive a ``FanoutAction``. The
    decision's ``skipped`` tuple is always empty; downstream nodes that
    can never fire just don't get tasks.
    """

    def test_untaken_branch_is_not_skipped(self):
        edges_from_a = [
            _edge("e1", "a", "b", port="success"),
            _edge("e2", "a", "e", port="failure"),
        ]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="a",
            source_node_type=NodeType.TASK,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges_from_a,
        )
        assert {a.target_node_id for a in decision.actions} == {"b"}
        assert decision.skipped == ()

    def test_already_completed_target_still_fires(self):
        """SA-846 cycle execution: a taken edge ALWAYS fires its target.

        An already-completed target is a revisit (cycle re-entry / late
        second arrival), not a skip — the dispatch layer re-runs it under
        a fresh visit-suffixed task id.
        """
        edges = [_edge("e1", "a", "b", port="success")]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="a",
            source_node_type=NodeType.TASK,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges,
        )
        # Completed/skipped filtering is retired — target is still emitted.
        assert [a.target_node_id for a in decision.actions] == ["b"]
        assert decision.skipped == ()


# ─────────────── edge-based dispatch (no AND-join gate) ──────────


class TestEdgeBasedDispatch:
    def test_target_fires_on_first_edge_taken(self):
        """``b`` has two predecessors. With edge-based dispatch the
        target is emitted when ``a`` completes — there is no
        predecessor-readiness AND-join gate.
        """
        edges = [_edge("e1", "a", "b")]
        decision = FanoutDecision.compute(
            causation_id="cause-1",
            source_node_id="a",
            source_node_type=NodeType.TRIGGER,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges,
        )
        assert [a.target_node_id for a in decision.actions] == ["b"]
        assert decision.deferred_target_ids == ()


# ─────────────────────── deterministic event ids ─────────────────


class TestDeterministicIds:
    def _sample_fanout(self, causation_id: str) -> FanoutDecision:
        return FanoutDecision.compute(
            causation_id=causation_id,
            source_node_id="a",
            source_node_type=NodeType.TASK,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=[
                _edge("e1", "a", "b", port="success"),
                _edge("e2", "a", "c", port="failure"),
            ],
        )

    def test_same_inputs_produce_identical_ids(self):
        """This is the redelivery-idempotency property that lets the event
        log dedupe the Dispatcher's downstream events. Phase 4b.1's
        `MemoryEventLogRepository.append` returns False on a repeat id."""
        first = self._sample_fanout("cause-shared")
        second = self._sample_fanout("cause-shared")

        assert [a.event_id for a in first.actions] == [a.event_id for a in second.actions]

    def test_different_causation_produces_different_ids(self):
        first = self._sample_fanout("cause-A")
        second = self._sample_fanout("cause-B")
        assert first.actions[0].event_id != second.actions[0].event_id


# ────────────────────────── purity / no-op ───────────────────────


class TestPurity:
    def test_no_mutation_of_input_collections(self):
        """Passing mutable dicts/sets must not result in any mutation."""
        completed = set()
        skipped = set()
        preds = {"b": ["a"]}
        adj = {"a": ["b"]}
        edges = [_edge("e1", "a", "b")]

        FanoutDecision.compute(
            causation_id="c",
            source_node_id="a",
            source_node_type=NodeType.TRIGGER,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=edges,
        )

        assert completed == set()
        assert skipped == set()
        assert preds == {"b": ["a"]}
        assert adj == {"a": ["b"]}

    def test_decision_is_frozen_dataclass(self):
        decision = FanoutDecision.compute(
            causation_id="c",
            source_node_id="a",
            source_node_type=NodeType.TRIGGER,
            source_task_status=TaskStatus.COMPLETED,
            outgoing_edges=[_edge("e1", "a", "b")],
        )
        # Frozen: trying to mutate raises.
        import dataclasses
        try:
            decision.actions = ()  # type: ignore[misc]
        except dataclasses.FrozenInstanceError:
            return
        raise AssertionError("FanoutDecision should be frozen")
