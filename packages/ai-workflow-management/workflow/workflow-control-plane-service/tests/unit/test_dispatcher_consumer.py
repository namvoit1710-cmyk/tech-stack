"""Phase 2b unit tests for the DispatcherConsumer.

Verifies:
- Port-routed ``NodeOutputProduced`` events produce the same
  ``EdgeTraversed`` event_ids as the engine's inline path, because both
  sides call ``compute_fanout(causation_id=event.event_id)`` with
  identical inputs.
- Branching ``taken_edge_ports`` events produce ``EdgeTraversed`` events
  whose ids match the inline branching path's
  ``EventId.derive(task.id, f"edge:{edge.id}:{target_id}")``.
- Redelivery of the same ``NodeOutputProduced`` produces byte-identical
  emissions (deterministic ids).
- The parity recorder captures drift when inline and dispatcher streams
  diverge.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.layer1_domain.entities.workflow import WorkflowEdge
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer1_domain.events import (
    EventTopic,
    LoopBodyDispatched,
    LoopExitDispatched,
    NodeOutputProduced,
)
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer4_frameworks.dispatcher import (
    DispatcherConsumer,
    DispatcherParityRecorder,
)


# ---- test doubles ----


class _FakeNode:
    def __init__(self, node_id: str, node_type: NodeType = NodeType.TASK) -> None:
        self.id = node_id
        self.node_type = node_type
        self.name = node_id


class _FakeWorkflow:
    def __init__(self, nodes: list[_FakeNode]) -> None:
        self._nodes = {n.id: n for n in nodes}

    def get_node(self, nid: str) -> _FakeNode | None:
        return self._nodes.get(nid)


class _FakeView:
    def __init__(self, topology: GraphTopology) -> None:
        self.topology = topology
        self.completed_nodes: frozenset[str] = frozenset()
        self.skipped_nodes: frozenset[str] = frozenset()


class _FakeViewStore:
    def __init__(self, view: _FakeView) -> None:
        self._view = view

    def get(self, run_id: str) -> _FakeView:
        return self._view


class _FakeWorkflowRepo:
    def __init__(self, workflow: _FakeWorkflow) -> None:
        self._workflow = workflow

    def get_by_id(self, wid: str) -> _FakeWorkflow:
        return self._workflow


class _FakePublisher:
    """Captures (topic, event) tuples for assertion in dispatcher-mode tests."""

    def __init__(self) -> None:
        self.emissions: list[tuple[str, Any]] = []

    def publish(self, topic: str, event: Any) -> None:
        self.emissions.append((topic, event))


def _make_linear_dag() -> tuple[_FakeWorkflow, GraphTopology, _FakeView]:
    """A → B with a single outgoing edge. Port-routed TASK at A."""
    a = _FakeNode("A", NodeType.TASK)
    b = _FakeNode("B", NodeType.TASK)
    edge = WorkflowEdge(
        id="e-ab", source_node_id="A", target_node_id="B",
        source_port_id="success", target_port_id="", label="", description="",
    )
    topology = GraphTopology(
        adjacency={"A": ["B"], "B": []},
        predecessors={"A": [], "B": ["A"]},
        edge_map={"A": [edge], "B": []},
        entry_nodes=["A"],
        leaf_nodes=["B"],
    )
    view = _FakeView(topology)
    workflow = _FakeWorkflow([a, b])
    return workflow, topology, view


def _make_loop_dag() -> tuple[_FakeWorkflow, GraphTopology, _FakeView]:
    """LOOP → body (B), LOOP → exit (E). Two outgoing edges on the
    loop node, distinguished by target. Body → LOOP edge omitted
    because the dispatcher only routes on LOOP → * edges."""
    loop = _FakeNode("LOOP", NodeType.LOOP)
    body = _FakeNode("B", NodeType.TASK)
    exit_node = _FakeNode("E", NodeType.TASK)
    body_edge = WorkflowEdge(
        id="e-loop-body", source_node_id="LOOP", target_node_id="B",
        source_port_id="", target_port_id="", label="", description="",
    )
    exit_edge = WorkflowEdge(
        id="e-loop-exit", source_node_id="LOOP", target_node_id="E",
        source_port_id="", target_port_id="", label="", description="",
    )
    topology = GraphTopology(
        adjacency={"LOOP": ["B", "E"], "B": [], "E": []},
        predecessors={"LOOP": [], "B": ["LOOP"], "E": ["LOOP"]},
        edge_map={"LOOP": [body_edge, exit_edge], "B": [], "E": []},
        entry_nodes=["LOOP"],
        leaf_nodes=["E"],
    )
    view = _FakeView(topology)
    workflow = _FakeWorkflow([loop, body, exit_node])
    return workflow, topology, view


class TestDispatcherConsumer:
    def test_port_routed_produces_deterministic_edge_event_id(self) -> None:
        """Both inline path and dispatcher should compute the same
        ``EdgeTraversed`` event_id because ``compute_fanout`` is pure."""
        workflow, topology, view = _make_linear_dag()
        recorder = DispatcherParityRecorder()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=recorder,
        )

        event = NodeOutputProduced(
            event_id="cause-1",
            run_id="run-1",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": 42}},
            output_port="success",
        )
        consumer.on_node_output_produced(event)

        # Recorder captured under dispatcher side
        recorded = recorder._dispatcher.get("run-1", {})
        edge_key = (EventTopic.EDGE_TRAVERSED,
                    EventId.derive("cause-1", "edge:e-ab:B"))
        assert edge_key in recorded, f"Expected edge event, got keys: {list(recorded.keys())}"

    def test_branching_taken_edge_ports_emits_edges(self) -> None:
        """CONDITION / SWITCH: taken_edge_ports populated → dispatcher
        emits ``EdgeTraversed`` with the deterministic id formula
        ``edge:<edge.id>:<target>``.
        """
        workflow, topology, view = _make_linear_dag()
        recorder = DispatcherParityRecorder()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=recorder,
        )

        event = NodeOutputProduced(
            event_id="cause-2",
            run_id="run-2",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": {}}},
            output_port="",
            taken_edge_ports={"e-ab": "branch-taken"},
        )
        consumer.on_node_output_produced(event)

        recorded = recorder._dispatcher.get("run-2", {})
        expected = EventId.derive("cause-2", "edge:e-ab:B")
        assert (EventTopic.EDGE_TRAVERSED, expected) in recorded

    def test_human_action_reject_emits_only_reject_edge(self) -> None:
        """Regression: HA with port-id'd confirm/reject edges and user
        sending ``_action: reject``. ResumePendingTaskUseCase populates
        ``taken_edge_ports`` with just the reject edge → dispatcher must
        emit EdgeTraversed for reject and skip confirm. Before the fix,
        ``output_port`` was hardcoded to "success" and the dispatcher's
        port-filter dropped BOTH port-id'd edges, stalling the run.
        """
        ha = _FakeNode("HA", NodeType.HUMAN_ACTION)
        confirm_target = _FakeNode("approved", NodeType.TASK)
        reject_target = _FakeNode("rejected", NodeType.TASK)
        confirm_edge = WorkflowEdge(
            id="e-confirm", source_node_id="HA", target_node_id="approved",
            source_port_id="confirm", target_port_id="", label="", description="",
        )
        reject_edge = WorkflowEdge(
            id="e-reject", source_node_id="HA", target_node_id="rejected",
            source_port_id="reject", target_port_id="", label="", description="",
        )
        topology = GraphTopology(
            adjacency={"HA": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"HA": [], "approved": ["HA"], "rejected": ["HA"]},
            edge_map={"HA": [confirm_edge, reject_edge], "approved": [], "rejected": []},
            entry_nodes=["HA"],
            leaf_nodes=["approved", "rejected"],
        )
        view = _FakeView(topology)
        workflow = _FakeWorkflow([ha, confirm_target, reject_target])
        publisher = _FakePublisher()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            publisher=publisher,
            mode="authoritative",
        )

        event = NodeOutputProduced(
            event_id="cause-ha-reject",
            run_id="run-ha",
            workflow_id="wf-1",
            node_id="HA",
            state_updates={"HA": {"_action": "reject"}},
            output_port="reject",
            taken_edge_ports={"e-reject": "reject"},
        )
        consumer.on_node_output_produced(event)

        edge_topics = [
            (t, e) for (t, e) in publisher.emissions
            if t == EventTopic.EDGE_TRAVERSED
        ]
        assert len(edge_topics) == 1, (
            f"Exactly one EdgeTraversed expected (reject), got {edge_topics}"
        )
        emitted = edge_topics[0][1]
        assert emitted.edge_id == "e-reject"
        assert emitted.target_node_id == "rejected"
        assert emitted.source_port_id == "reject"

    def test_redelivery_produces_identical_emissions(self) -> None:
        """Handing the consumer the same ``NodeOutputProduced`` twice
        must produce byte-identical emissions — deterministic ids
        guarantee the event log dedupes on replay.
        """
        workflow, topology, view = _make_linear_dag()
        recorder = DispatcherParityRecorder()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=recorder,
        )
        event = NodeOutputProduced(
            event_id="cause-3",
            run_id="run-3",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": 1}},
            output_port="success",
        )
        consumer.on_node_output_produced(event)
        first = dict(recorder._dispatcher.get("run-3", {}))
        consumer.on_node_output_produced(event)
        second = dict(recorder._dispatcher.get("run-3", {}))

        assert first == second, (
            f"Redelivery must produce identical emissions. First={first} "
            f"Second={second}"
        )

    def test_bootstrap_node_id_produces_no_fan_out(self) -> None:
        """The ``__bootstrap__`` synthetic node that seeds variables
        should never drive fan-out."""
        workflow, topology, view = _make_linear_dag()
        recorder = DispatcherParityRecorder()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=recorder,
        )
        event = NodeOutputProduced(
            event_id="bootstrap-1",
            run_id="run-4",
            workflow_id="wf-1",
            node_id="__bootstrap__",
            state_updates={"input": {"x": 1}},
        )
        consumer.on_node_output_produced(event)
        assert recorder._dispatcher.get("run-4", {}) == {}

    def test_dispatcher_mode_publishes_via_publisher(self) -> None:
        """Phase 3a PR 2: under ``mode="dispatcher"`` the consumer
        publishes each emission through the injected publisher.

        Production bootstrap does NOT wire a recorder in dispatcher mode
        (the recorder only exists in shadow mode as a one-release
        diagnostic safety net). Callers that provide a recorder
        regardless will have emissions mirrored there too — that is the
        behaviour the ``shadow`` bootstrap relies on.
        """
        workflow, topology, view = _make_linear_dag()
        publisher = _FakePublisher()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=None,  # matches production dispatcher-mode wiring
            publisher=publisher,
            mode="dispatcher",
        )
        event = NodeOutputProduced(
            event_id="cause-auth",
            run_id="run-auth",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": 42}},
            output_port="success",
        )
        consumer.on_node_output_produced(event)

        # Publisher saw EdgeTraversed + NodeInput for A → B.
        topics = [topic for (topic, _) in publisher.emissions]
        assert EventTopic.EDGE_TRAVERSED in topics
        assert EventTopic.NODE_INPUT in topics

    def test_authoritative_without_publisher_falls_back_to_recorder(
        self,
    ) -> None:
        """Defensive: if the bootstrap path forgets to ``set_publisher``
        in dispatcher mode, the consumer must not crash. It silently
        records to the recorder so the comparator can still show the
        misconfiguration as drift rather than taking the process down.
        """
        workflow, topology, view = _make_linear_dag()
        recorder = DispatcherParityRecorder()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=recorder,
            publisher=None,  # oops
            mode="dispatcher",
        )
        event = NodeOutputProduced(
            event_id="cause-nopub",
            run_id="run-nopub",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": 1}},
            output_port="success",
        )
        consumer.on_node_output_produced(event)
        # Still recorded — comparator will flag this as inline-only drift.
        assert recorder._dispatcher.get("run-nopub", {}) != {}

    def test_set_publisher_late_binds(self) -> None:
        """Bootstrap creates the tee *after* the consumer, then sets
        the publisher via ``set_publisher``. Emissions published after
        ``set_publisher`` go through the bound publisher.
        """
        workflow, topology, view = _make_linear_dag()
        publisher = _FakePublisher()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=None,
            publisher=None,
            mode="dispatcher",
        )
        consumer.set_publisher(publisher)
        event = NodeOutputProduced(
            event_id="cause-late",
            run_id="run-late",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": 1}},
            output_port="success",
        )
        consumer.on_node_output_produced(event)
        assert publisher.emissions, "publisher should have received emissions"

    def test_loop_body_dispatched_emits_one_edge_per_body(self) -> None:
        """Phase 3a.3: LoopBodyDispatched carrying two body node ids
        produces exactly two EdgeTraversed events — one per matching
        LOOP → body edge. ``iteration_index`` flows through to the
        emitted event so frontend animations highlight the right
        iteration.
        """
        workflow, topology, view = _make_loop_dag()
        publisher = _FakePublisher()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=None,
            publisher=publisher,
            mode="dispatcher",
        )
        event = LoopBodyDispatched(
            event_id="loop-cause",
            run_id="run-loop",
            workflow_id="wf-1",
            loop_node_id="LOOP",
            body_node_ids=["B"],
            iteration_index=3,
            causation_id="task-loop-1",
        )
        consumer.on_loop_body_dispatched(event)

        edge_topics = [t for (t, _) in publisher.emissions]
        assert edge_topics == [EventTopic.EDGE_TRAVERSED]
        emitted = publisher.emissions[0][1]
        assert emitted.edge_id == "e-loop-body"
        assert emitted.source_node_id == "LOOP"
        assert emitted.target_node_id == "B"
        assert emitted.iteration_index == 3
        assert emitted.source == "dispatcher"
        # Deterministic id keyed on causation
        assert emitted.event_id == EventId.derive(
            "task-loop-1", "edge:e-loop-body:B",
        )

    def test_loop_exit_dispatched_emits_exit_edge(self) -> None:
        """Phase 3a.3: LoopExitDispatched fires after the last
        iteration; the consumer emits exactly one EdgeTraversed for
        each LOOP → exit edge and omits ``iteration_index`` because
        exit paths are not iteration-scoped.
        """
        workflow, topology, view = _make_loop_dag()
        publisher = _FakePublisher()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=None,
            publisher=publisher,
            mode="dispatcher",
        )
        event = LoopExitDispatched(
            event_id="loop-exit-cause",
            run_id="run-loop",
            workflow_id="wf-1",
            loop_node_id="LOOP",
            exit_node_ids=["E"],
            causation_id="task-loop-final",
        )
        consumer.on_loop_exit_dispatched(event)

        assert len(publisher.emissions) == 1
        topic, emitted = publisher.emissions[0]
        assert topic == EventTopic.EDGE_TRAVERSED
        assert emitted.edge_id == "e-loop-exit"
        assert emitted.target_node_id == "E"
        assert emitted.iteration_index is None
        assert emitted.event_id == EventId.derive(
            "task-loop-final", "edge:e-loop-exit:E",
        )

    def test_loop_body_redelivery_is_deterministic(self) -> None:
        """Handing the consumer the same LoopBodyDispatched twice
        produces byte-identical EdgeTraversed emissions — the event
        log dedupes on the deterministic id, so redelivery is safe.
        """
        workflow, topology, view = _make_loop_dag()
        publisher = _FakePublisher()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=None,
            publisher=publisher,
            mode="dispatcher",
        )
        event = LoopBodyDispatched(
            event_id="loop-cause-2",
            run_id="run-loop-2",
            workflow_id="wf-1",
            loop_node_id="LOOP",
            body_node_ids=["B"],
            iteration_index=0,
            causation_id="task-loop-2",
        )
        consumer.on_loop_body_dispatched(event)
        consumer.on_loop_body_dispatched(event)
        assert len(publisher.emissions) == 2
        first_id = publisher.emissions[0][1].event_id
        second_id = publisher.emissions[1][1].event_id
        assert first_id == second_id


class TestDispatcherParityRecorder:
    def test_finalize_zero_drift_when_streams_agree(self) -> None:
        recorder = DispatcherParityRecorder()
        workflow, topology, view = _make_linear_dag()
        consumer = DispatcherConsumer(
            view_store=_FakeViewStore(view),
            workflow_repo=_FakeWorkflowRepo(workflow),
            recorder=recorder,
        )
        event = NodeOutputProduced(
            event_id="cause-x",
            run_id="run-x",
            workflow_id="wf-1",
            node_id="A",
            state_updates={"A": {"output": 1}},
            output_port="success",
        )
        consumer.on_node_output_produced(event)

        # Simulate the inline path observing the same EdgeTraversed.
        expected_edge_id = EventId.derive("cause-x", "edge:e-ab:B")
        inline_edge_event = SimpleNamespace(
            event_id=expected_edge_id,
            edge_id="e-ab",
            source_node_id="A",
            target_node_id="B",
            source_port_id="success",
        )
        recorder.record_inline(
            "run-x", EventTopic.EDGE_TRAVERSED, inline_edge_event,
        )

        # Both sides also observed a NodeInput for target B with the
        # same deterministic id.
        inline_input_event = SimpleNamespace(
            event_id=EventId.derive("cause-x", "input:B"),
            node_id="B",
        )
        recorder.record_inline(
            "run-x", EventTopic.NODE_INPUT, inline_input_event,
        )

        drifts = recorder.finalize("run-x")
        assert drifts == []
        assert recorder.total == 0

    def test_finalize_detects_inline_only_emission(self) -> None:
        recorder = DispatcherParityRecorder()
        inline_edge_event = SimpleNamespace(
            event_id="only-in-inline",
            edge_id="e-xy",
            source_node_id="X",
            target_node_id="Y",
            source_port_id="success",
        )
        recorder.record_inline(
            "run-z", EventTopic.EDGE_TRAVERSED, inline_edge_event,
        )
        drifts = recorder.finalize("run-z")
        assert len(drifts) == 1
        assert drifts[0].kind == "inline_only"
        assert drifts[0].event_id == "only-in-inline"
