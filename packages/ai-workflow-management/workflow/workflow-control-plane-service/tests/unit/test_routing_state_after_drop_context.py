"""Resumed PENDING nodes must route down ONE branch after a reload.

``last_output_ports`` / ``last_taken_edge_ports`` rode
``AIW_WORKFLOW_RUNS.CONTEXT``. Step 5b dropped the column, and unlike
``loop_state`` / ``node_visits`` nothing was left to rebuild them, so the
orchestration worker's reload sees ``{}`` and ``decide_successors`` falls
through to "traverse every outgoing edge" — a rejected HUMAN_ACTION also fires
its confirm branch. Silent: no error, no failed task, both subtrees just run.

Harness invariant: every run here is arranged with an EMPTY ``RunContext`` and
the truth placed in a projector double, because that is the only shape that can
fail. A test that writes the ports onto ``run.context`` and reads them back is
testing a round trip HANA no longer performs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.orchestration.orchestration_engine import (
    OrchestrationEngine,
)

HA = "ha-node"
CONFIRM_T, REJECT_T = "confirm-target", "reject-target"
E_CONFIRM, E_REJECT = "edge-confirm", "edge-reject"


class ViewDouble:
    """Projector-shaped store. Deliberately NOT ``LiveViewStoreDouble`` — that
    one reads the run repo, which is empty in exactly this scenario."""

    def __init__(self, ports=None, taken=None, raises=False):
        self._ports = ports or {}
        self._taken = taken or {}
        self._raises = raises

    def get(self, run_id: str):
        if self._raises:
            raise RuntimeError("event log unavailable")
        # Pass values through RAW — coercing here would sanitise malformed
        # entries before the code under test ever sees them.
        return SimpleNamespace(
            last_output_ports=dict(self._ports),
            last_taken_edge_ports=dict(self._taken),
        )


def _workflow(*, ports_on_edges: bool) -> Workflow:
    nodes = [
        WorkflowNode(id=HA, name="Approve", node_type=NodeType.HUMAN_ACTION),
        WorkflowNode(id=CONFIRM_T, name="On confirm", node_type=NodeType.TASK,
                     worker_type="http-worker"),
        WorkflowNode(id=REJECT_T, name="On reject", node_type=NodeType.TASK,
                     worker_type="http-worker"),
    ]
    edges = [
        WorkflowEdge(id=E_CONFIRM, source_node_id=HA, target_node_id=CONFIRM_T,
                     source_port_id="confirm" if ports_on_edges else ""),
        # The legacy shape that started this: labelled, but carrying NO port id,
        # so a port filter alone reads it as "port-less" and lets it through.
        WorkflowEdge(id=E_REJECT, source_node_id=HA, target_node_id=REJECT_T,
                     source_port_id="reject" if ports_on_edges else "",
                     condition=None if ports_on_edges else "reject"),
    ]
    return Workflow(id="wf-1", name="wf", nodes=nodes, edges=edges)


def _reloaded_run() -> WorkflowRun:
    """A run as the orchestration worker gets it back from HANA today."""
    run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext())
    assert run.context.last_output_ports == {}
    assert run.context.last_taken_edge_ports == {}
    return run


def _engine(view) -> OrchestrationEngine:
    eng = OrchestrationEngine.__new__(OrchestrationEngine)
    eng._view_store = view
    return eng


class TestEnsureRoutingState:
    def test_fills_both_maps_from_the_projector(self):
        run = _reloaded_run()
        _engine(ViewDouble(
            ports={HA: "reject"},
            taken={HA: {E_REJECT: "reject"}},
        ))._ensure_routing_state(run)
        assert run.context.last_output_ports == {HA: "reject"}
        assert run.context.last_taken_edge_ports == {HA: {E_REJECT: "reject"}}

    def test_an_in_frame_write_beats_a_lagging_view(self):
        run = _reloaded_run()
        run.context.last_output_ports[HA] = "confirm"
        run.context.last_taken_edge_ports[HA] = {E_CONFIRM: "confirm"}
        _engine(ViewDouble(ports={HA: "reject"}, taken={HA: {E_REJECT: "reject"}}))\
            ._ensure_routing_state(run)
        assert run.context.last_output_ports == {HA: "confirm"}
        assert run.context.last_taken_edge_ports == {HA: {E_CONFIRM: "confirm"}}

    def test_fills_one_map_when_only_the_other_was_written(self):
        run = _reloaded_run()
        run.context.last_output_ports[HA] = "confirm"
        _engine(ViewDouble(ports={HA: "reject"}, taken={HA: {E_REJECT: "reject"}}))\
            ._ensure_routing_state(run)
        assert run.context.last_output_ports == {HA: "confirm"}   # untouched
        assert run.context.last_taken_edge_ports == {HA: {E_REJECT: "reject"}}

    def test_no_view_store_is_a_noop(self):
        run = _reloaded_run()
        _engine(None)._ensure_routing_state(run)
        assert run.context.last_output_ports == {}

    def test_view_returning_none_is_a_noop(self):
        run = _reloaded_run()
        eng = _engine(SimpleNamespace(get=lambda _rid: None))
        eng._ensure_routing_state(run)
        assert run.context.last_output_ports == {}

    def test_a_failing_view_store_warns_but_does_not_raise(self, caplog):
        """Routing happens either way; turning this into a run failure is worse.
        But it must be visible — degrading silently is the original bug."""
        run = _reloaded_run()
        with caplog.at_level("WARNING"):
            _engine(ViewDouble(raises=True))._ensure_routing_state(run)
        assert run.context.last_output_ports == {}
        assert any("routing-state refill failed" in r.message for r in caplog.records)

    def test_empty_projector_leaves_empty_maps(self):
        run = _reloaded_run()
        _engine(ViewDouble())._ensure_routing_state(run)
        assert run.context.last_output_ports == {}
        assert run.context.last_taken_edge_ports == {}

    def test_malformed_view_entries_are_skipped_not_crashed(self):
        run = _reloaded_run()
        _engine(ViewDouble(ports={HA: "reject"}, taken={HA: "not-a-dict"}))\
            ._ensure_routing_state(run)
        assert run.context.last_output_ports == {HA: "reject"}
        assert run.context.last_taken_edge_ports == {}


class TestDecideSuccessorsUsesTheRefilledState:
    """The payoff: with the maps refilled, only the taken branch fans out."""

    def _task(self):
        from app.layer1_domain.entities.task import Task
        from app.layer1_domain.value_objects.task_status import TaskStatus

        return Task(id="t1", run_id="run-1", node_id=HA, status=TaskStatus.COMPLETED)

    def _decide(self, view, *, ports_on_edges: bool):
        workflow = _workflow(ports_on_edges=ports_on_edges)
        run = _reloaded_run()
        run.context.topology = GraphTopology.from_workflow(workflow)
        _engine(view)._ensure_routing_state(run)
        return run.decide_successors(self._task(), workflow, run.context.topology)

    def _targets(self, decision):
        return {a.target_node_id for a in decision.actions}

    def test_reject_does_not_fan_out_the_confirm_branch(self):
        decision = self._decide(
            ViewDouble(ports={HA: "reject"}, taken={HA: {E_REJECT: "reject"}}),
            ports_on_edges=True,
        )
        assert self._targets(decision) == {REJECT_T}

    def test_taken_edge_set_beats_a_portless_legacy_edge(self):
        """The confirm edge carries no port id, so a port filter alone passes
        it. Only the recorded taken-edge set excludes it."""
        decision = self._decide(
            ViewDouble(ports={HA: "reject"}, taken={HA: {E_REJECT: "reject"}}),
            ports_on_edges=False,
        )
        assert self._targets(decision) == {REJECT_T}

    def test_out_default_is_not_treated_as_an_override(self):
        """``out_default`` is the traverse-normally sentinel — it must not
        become a port filter that suppresses every edge."""
        decision = self._decide(
            ViewDouble(ports={HA: "out_default"}), ports_on_edges=True,
        )
        assert self._targets(decision) == {CONFIRM_T, REJECT_T}

    def test_without_the_refill_both_branches_run(self):
        """Pins the bug itself: this is today's behaviour on `solace`, and the
        reason the refill exists. If this ever stops fanning out both, the
        no-view-store path changed and the tests above prove less than they look."""
        decision = self._decide(ViewDouble(), ports_on_edges=True)
        assert self._targets(decision) == {CONFIRM_T, REJECT_T}
