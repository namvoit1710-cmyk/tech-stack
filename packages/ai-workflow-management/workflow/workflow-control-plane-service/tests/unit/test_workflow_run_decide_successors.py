"""Pure unit tests for ``WorkflowRun.decide_successors``.

The ``decide`` half of the successor-routing seam (slice 2 of the
decide/evolve refactor — see ``docs/design/run-aggregate-decide-evolve-seam.md``).
It takes the completed task, the workflow, the prepared topology, and a
snapshot of completed nodes, and returns a ``FanoutDecision`` — no repos, no
publishers, no async, no mocks.

The method's *own* logic is the SA-846 PENDING port-override pre-routing
(``last_output_ports`` / ``last_taken_edge_ports``) plus the source-node-type
fallback; it then delegates the actual port filtering to
``FanoutDecision.compute`` (whose port semantics are exhaustively covered in
``test_dispatcher_fanout.py``). These tests target the decide_successors
boundary: that the pre-routing is applied for PENDING nodes, ignored for
others, and that delegation threads through correctly.
"""

from __future__ import annotations

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import (
    WorkflowRun,
    RunContext,
    GraphTopology,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus


def _run(**ctx) -> WorkflowRun:
    return WorkflowRun(
        id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING,
        context=RunContext(**ctx),
    )


def _task(node_id: str, status: TaskStatus = TaskStatus.COMPLETED, **kw) -> Task:
    return Task(id=f"t-{node_id}", run_id="run-1", node_id=node_id, status=status, **kw)


def _node(node_id: str, node_type: NodeType = NodeType.TASK) -> WorkflowNode:
    return WorkflowNode(id=node_id, node_type=node_type)


def _edge(edge_id: str, src: str, tgt: str, port: str = "") -> WorkflowEdge:
    return WorkflowEdge(
        id=edge_id, source_node_id=src, target_node_id=tgt, source_port_id=port,
    )


def _workflow(nodes: list[WorkflowNode], edges: list[WorkflowEdge]) -> Workflow:
    return Workflow(id="wf-1", workflow_id="wf-1", nodes=nodes, edges=edges)


def _topology(edges: list[WorkflowEdge]) -> GraphTopology:
    edge_map: dict[str, list[WorkflowEdge]] = {}
    adjacency: dict[str, list[str]] = {}
    predecessors: dict[str, list[str]] = {}
    for e in edges:
        edge_map.setdefault(e.source_node_id, []).append(e)
        adjacency.setdefault(e.source_node_id, []).append(e.target_node_id)
        predecessors.setdefault(e.target_node_id, []).append(e.source_node_id)
    return GraphTopology(
        adjacency=adjacency, predecessors=predecessors, edge_map=edge_map,
    )


def _targets(decision) -> set[str]:
    return {a.target_node_id for a in decision.actions}


# ── delegation: port filtering via compute_fanout ───────────────────────

def test_task_completed_routes_success_port_only():
    edges = [_edge("e1", "a", "b", "success"), _edge("e2", "a", "c", "failure")]
    run = _run()
    decision = run.decide_successors(
        _task("a", TaskStatus.COMPLETED),
        _workflow([_node("a", NodeType.TASK)], edges),
        _topology(edges),
    )
    assert decision.expected_port == "success"
    assert _targets(decision) == {"b"}


def test_task_failed_routes_failure_port_only():
    edges = [_edge("e1", "a", "b", "success"), _edge("e2", "a", "c", "failure")]
    run = _run()
    decision = run.decide_successors(
        _task("a", TaskStatus.FAILED),
        _workflow([_node("a", NodeType.TASK)], edges),
        _topology(edges),
    )
    assert decision.expected_port == "failure"
    assert _targets(decision) == {"c"}


def test_non_port_routed_node_emits_all_edges():
    edges = [_edge("e1", "trig", "a"), _edge("e2", "trig", "b")]
    run = _run()
    decision = run.decide_successors(
        _task("trig", TaskStatus.COMPLETED),
        _workflow([_node("trig", NodeType.TRIGGER)], edges),
        _topology(edges),
    )
    assert decision.expected_port == ""
    assert _targets(decision) == {"a", "b"}


def test_missing_source_node_falls_back_to_task_type():
    # workflow.get_node returns None -> NodeType.TASK fallback -> port-routed.
    edges = [_edge("e1", "a", "b", "success"), _edge("e2", "a", "c", "failure")]
    run = _run()
    decision = run.decide_successors(
        _task("a", TaskStatus.COMPLETED),
        _workflow([], edges),  # no node "a"
        _topology(edges),
    )
    assert decision.expected_port == "success"
    assert _targets(decision) == {"b"}


# ── SA-846 PENDING pre-routing: last_output_ports override ───────────────

def test_pending_node_output_port_override_applied():
    # HUMAN_ACTION resumed as "reject": only the reject + port-less edges fire,
    # NOT the confirm edge — even though HUMAN_ACTION isn't port-routed.
    edges = [
        _edge("e1", "ha", "confirm", "confirm"),
        _edge("e2", "ha", "reject", "reject"),
        _edge("e3", "ha", "always", ""),
    ]
    run = _run(last_output_ports={"ha": "reject"})
    decision = run.decide_successors(
        _task("ha", TaskStatus.COMPLETED),
        _workflow([_node("ha", NodeType.HUMAN_ACTION)], edges),
        _topology(edges),
    )
    assert decision.expected_port == "reject"
    assert _targets(decision) == {"reject", "always"}


def test_pending_node_out_default_does_not_override():
    # "out_default" is the sentinel for "no explicit port" -> no override, so
    # the non-port-routed PENDING node traverses all edges.
    edges = [_edge("e1", "ha", "b", "confirm"), _edge("e2", "ha", "c", "")]
    run = _run(last_output_ports={"ha": "out_default"})
    decision = run.decide_successors(
        _task("ha", TaskStatus.COMPLETED),
        _workflow([_node("ha", NodeType.HUMAN_ACTION)], edges),
        _topology(edges),
    )
    assert decision.expected_port == ""
    assert _targets(decision) == {"b", "c"}


def test_pending_node_recorded_taken_edges_filter():
    # The resume recorded the exact taken-edge set -> fan out over those only,
    # so the un-recorded edge does not fire.
    edges = [_edge("e1", "ha", "b", ""), _edge("e2", "ha", "c", "")]
    run = _run(last_taken_edge_ports={"ha": {"e1": "confirm"}})
    decision = run.decide_successors(
        _task("ha", TaskStatus.COMPLETED),
        _workflow([_node("ha", NodeType.HUMAN_ACTION)], edges),
        _topology(edges),
    )
    assert _targets(decision) == {"b"}


# ── pre-routing is PENDING-only ──────────────────────────────────────────

def test_non_pending_node_ignores_recorded_overrides():
    # A TASK is port-routed, NOT pending: its leftover last_output_ports /
    # last_taken_edge_ports must be ignored, so it routes purely by status.
    edges = [_edge("e1", "a", "b", "success"), _edge("e2", "a", "c", "failure")]
    run = _run(
        last_output_ports={"a": "reject"},
        last_taken_edge_ports={"a": {"e2": "x"}},
    )
    decision = run.decide_successors(
        _task("a", TaskStatus.COMPLETED),
        _workflow([_node("a", NodeType.TASK)], edges),
        _topology(edges),
    )
    assert decision.expected_port == "success"
    assert _targets(decision) == {"b"}
