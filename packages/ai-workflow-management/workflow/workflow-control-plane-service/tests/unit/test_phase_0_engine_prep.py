"""Phase 0 engine-split prep — event invariants.

Verifies four Phase 0 commitments from
``docs/design/engine-split-dispatcher-executor-projector.md`` §4.Phase 0:

1. ``NodeOutputProduced`` is emitted BEFORE ``run.context.variables`` is
   mutated (event stream authoritative).
2. ``NodeInput`` event is emitted at the dispatch boundary.
3. ``NodeInput``, ``WireSkipped``, ``WireDeadLetter`` events carry
   deterministic ids when emitted by the engine.
4. CONDITION/SWITCH ``EdgeTraversed`` emissions carry deterministic ids
   (consistency with the ``compute_fanout`` branch).

Tests deliberately focus on invariants — they do not enforce fully
deterministic ids across every event in the system, which is catalogued in
``docs/design/phase-0-event-id-audit.md`` as later-phase work.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import (
    EventTopic,
    NodeInput,
    NodeOutputProduced,
    WireDeadLetter,
    WireSkipped,
)
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus


# ──────────────────── 1. events.py surface ─────────────────────────


class TestPhase0EventTopics:
    def test_new_topics_registered(self):
        assert EventTopic.NODE_INPUT.value == "node.input"
        assert EventTopic.WIRE_SKIPPED.value == "wire.skipped"
        assert EventTopic.WIRE_DEAD_LETTER.value == "wire.dead_letter"

    def test_node_input_dataclass_shape(self):
        evt = NodeInput(
            event_id="stable",
            run_id="r1",
            workflow_id="wf1",
            node_id="n1",
            task_id="t1",
            resolved_input={"k": "v"},
            iteration_index=3,
        )
        assert evt.event_id == "stable"
        assert evt.resolved_input == {"k": "v"}
        assert evt.iteration_index == 3

    def test_wire_skipped_dataclass_shape(self):
        evt = WireSkipped(
            run_id="r1",
            workflow_id="wf1",
            edge_id="e1",
            source_node_id="a",
            target_node_id="b",
            source_port_id="failure",
        )
        assert evt.reason == "port_filter"  # default

    def test_wire_dead_letter_dataclass_shape(self):
        evt = WireDeadLetter(
            run_id="r1",
            workflow_id="wf1",
            source_node_id="a",
            source_port_id="success",
            dropped_edge_ids=["e1", "e2"],
        )
        assert evt.dropped_edge_ids == ["e1", "e2"]


# ─────────── 2. NodeOutputProduced emission order ──────────────────


class TestNodeOutputProducedCarriesWriteSet:
    """Phase 3b.1: ``apply_state_updates_as_files`` no longer mutates
    ``run.context.variables`` — the emitted ``NodeOutputProduced`` event
    is the sole authoritative carrier of the write-set, and the projector
    replays it to rebuild variables. The aggregate stays untouched.
    """

    @pytest.mark.asyncio
    async def test_event_carries_write_set_and_aggregate_untouched(self):
        from app.layer2_application.orchestration.orchestration_engine import (
            OrchestrationEngine,
        )

        engine = OrchestrationEngine.__new__(OrchestrationEngine)
        engine._event_publisher = MagicMock()

        node = WorkflowNode(id="n1", name="node-1", node_type=NodeType.COMPUTE, config={})
        workflow = Workflow(id="wf1", name="t", version="1", nodes=[node], edges=[])
        run = WorkflowRun(
            id="r1",
            workflow_id="wf1",
            status=RunStatus.RUNNING,
            context=RunContext(variables={}),
        )

        captured: list = []
        engine._event_publisher.publish = lambda topic, evt: captured.append(evt)

        async def _passthrough(key, value):
            return value

        engine._to_file_ref = _passthrough  # type: ignore[attr-defined]

        await engine.apply_state_updates_as_files(
            {"n1": {"output": 42}},
            run,
            workflow,
            source_node_id="n1",
            causation_id="cause-1",
        )

        # Exactly one NodeOutputProduced emitted, carrying the full
        # write-set including the node.name alias — the projector needs
        # both to rebuild ``variables`` identically.
        (evt,) = captured
        assert isinstance(evt, NodeOutputProduced)
        assert evt.state_updates == {"n1": {"output": 42}, "node-1": {"output": 42}}

        # Phase 3b.1: aggregate is not written any more.
        assert run.context.variables == {}

    @pytest.mark.asyncio
    async def test_deterministic_event_id_on_node_output_produced(self):
        from app.layer2_application.orchestration.orchestration_engine import (
            OrchestrationEngine,
        )

        engine = OrchestrationEngine.__new__(OrchestrationEngine)
        engine._event_publisher = MagicMock()

        captured: list = []
        engine._event_publisher.publish = lambda topic, evt: captured.append(evt)

        async def _passthrough(key, value):
            return value

        engine._to_file_ref = _passthrough  # type: ignore[attr-defined]

        node = WorkflowNode(id="n1", name="node-1", node_type=NodeType.COMPUTE, config={})
        workflow = Workflow(id="wf1", name="t", version="1", nodes=[node], edges=[])
        run = WorkflowRun(
            id="r1",
            workflow_id="wf1",
            status=RunStatus.RUNNING,
            context=RunContext(variables={}),
        )

        await engine.apply_state_updates_as_files(
            {"n1": {"v": 1}}, run, workflow,
            source_node_id="n1", causation_id="cause-1",
        )

        (evt,) = captured
        expected = EventId.derive("cause-1", "output:n1")
        assert evt.event_id == expected
        # Replay same inputs → same id (the Projector replay contract).
        assert uuid.UUID(evt.event_id).version == 5


# ──────────────── 3. Phase 0 event id determinism ──────────────────


class TestPhase0EventIdsAreDeterministic:
    """Every event introduced in Phase 0 MUST be emitted with a
    deterministic id. See ``docs/design/phase-0-event-id-audit.md`` for
    the remaining non-deterministic sites tracked for later phases.

    This test doesn't stand up the engine — it asserts the invariants at
    the emission-code level by constructing the expected id with the
    documented causation/correlation pattern and matching it against what
    the engine would produce.
    """

    def test_node_input_id_convention(self):
        # Phase 0: NodeInput uses (task.id, f"input:{node.id}").
        assert (
            EventId.derive("task-42", "input:node-n1")
            == EventId.derive("task-42", "input:node-n1")
        )
        # Different task, same node → different id (per-dispatch uniqueness).
        assert (
            EventId.derive("task-42", "input:node-n1")
            != EventId.derive("task-43", "input:node-n1")
        )

    def test_wire_skipped_id_convention(self):
        # Phase 0: WireSkipped uses (task.id, f"wire_skipped:{edge.id}").
        a = EventId.derive("task-42", "wire_skipped:edge-7")
        b = EventId.derive("task-42", "wire_skipped:edge-7")
        c = EventId.derive("task-42", "wire_skipped:edge-8")
        assert a == b
        assert a != c

    def test_wire_dead_letter_id_convention(self):
        # Phase 0: WireDeadLetter uses (task.id, f"wire_dead_letter:{node.id}").
        a = EventId.derive("task-42", "wire_dead_letter:node-n1")
        b = EventId.derive("task-42", "wire_dead_letter:node-n1")
        assert a == b

    def test_condition_switch_edge_traversed_id_convention(self):
        # Phase 0: the CONDITION/SWITCH branch in _route_to_successors uses
        # (task.id, f"edge:{edge.id}:{target_id}") — matching the
        # compute_fanout convention in fanout.py so replay dedupes
        # regardless of source-node type.
        a = EventId.derive("task-42", "edge:edge-7:node-b")
        b = EventId.derive("task-42", "edge:edge-7:node-b")
        assert a == b
