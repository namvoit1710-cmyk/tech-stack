"""Pure unit tests for ``WorkflowRun.decide_loop_iteration``.

The ``decide`` half of the loop iteration-vs-done seam (slice 4 of the
decide/evolve refactor — see ``docs/design/run-aggregate-decide-evolve-seam.md``).
It is the SA-846 routing-aware body-done BFS: given a loop node id, an iteration
index, the workflow, and the prepared topology, it returns ``True`` iff every
body-subgraph node REACHED by this iteration's routing is recorded in
``run.context.loop_state[loop_node_id]['iter_progress'][iter_idx]`` — no repos,
no queue, no async, no mocks. The loop shell
(``LoopIterationDriver.maybe_enqueue_loop_iteration``) uses the answer to decide
whether to enqueue the next ``LoopIterationAction``.

These mirror the established slice-1/2/3 pure-test pattern and re-target the
pre-existing ``TestIsIterationDone`` oracle (``test_loop_driver_body_subgraph.py``)
at the aggregate boundary — ``run.decide_loop_iteration(...)`` instead of the
free ``is_iteration_done(run=run, ...)``.
"""

from __future__ import annotations

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import (
    WorkflowRun,
    RunContext,
    GraphTopology,
)
from app.layer1_domain.value_objects.node_type import NodeType


def _run(loop_node_id: str, iter_progress_list: list[str], iter_idx: int = 0, **ctx) -> WorkflowRun:
    return WorkflowRun(
        id="run-1", workflow_id="wf",
        context=RunContext(
            loop_state={
                loop_node_id: {
                    "iter_progress": {iter_idx: list(iter_progress_list)},
                    "total": 3,
                    "completed_iterations": [],
                },
            },
            **ctx,
        ),
    )


def _node(node_id: str, node_type: NodeType = NodeType.TASK) -> WorkflowNode:
    return WorkflowNode(id=node_id, node_type=node_type, worker_type="w")


def _wf(nodes: list[WorkflowNode]) -> Workflow:
    return Workflow(id="wf", nodes=nodes, edges=[])


def _dag(edge_map: dict) -> GraphTopology:
    return GraphTopology(
        edge_map=edge_map, adjacency={}, predecessors={},
        entry_nodes=[], leaf_nodes=[],
    )


def _simple_body() -> tuple[Workflow, GraphTopology]:
    wf = _wf([
        _node("loop", NodeType.LOOP), _node("body"), _node("exit"),
    ])
    dag = _dag({
        "loop": [
            WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body"),
            WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
        ],
    })
    return wf, dag


# ── single-node body ────────────────────────────────────────────────────

def test_single_node_body_done():
    wf, dag = _simple_body()
    assert _run("loop", ["body"]).decide_loop_iteration("loop", 0, wf, dag) is True


def test_single_node_body_not_yet_done():
    wf, dag = _simple_body()
    assert _run("loop", []).decide_loop_iteration("loop", 0, wf, dag) is False


# ── multi-node body ───────────────────────────────────────────────────────

def _chain_body() -> tuple[Workflow, GraphTopology]:
    wf = _wf([
        _node("loop", NodeType.LOOP),
        _node("a", NodeType.COMPUTE), _node("b", NodeType.COMPUTE),
    ])
    dag = _dag({
        "loop": [WorkflowEdge(source_node_id="loop", target_node_id="a", condition="body")],
        "a": [WorkflowEdge(source_node_id="a", target_node_id="b")],
    })
    return wf, dag


def test_multi_node_body_all_complete():
    wf, dag = _chain_body()
    assert _run("loop", ["a", "b"]).decide_loop_iteration("loop", 0, wf, dag) is True


def test_multi_node_body_missing_leaf():
    wf, dag = _chain_body()
    assert _run("loop", ["a"]).decide_loop_iteration("loop", 0, wf, dag) is False


# ── edge cases ────────────────────────────────────────────────────────────

def test_no_loop_state_returns_false():
    wf, dag = _simple_body()
    run = WorkflowRun(id="run-1", workflow_id="wf", context=RunContext())
    assert run.decide_loop_iteration("loop", 0, wf, dag) is False


def test_empty_body_returns_true():
    # Degenerate loop with no body-entry node → trivially done.
    wf = _wf([_node("loop", NodeType.LOOP)])
    assert _run("loop", []).decide_loop_iteration("loop", 0, wf, _dag({})) is True


# ── branching body: taken-edge routing (SA-846) ──────────────────────────

def test_branching_body_untaken_branch_not_required():
    # CONDITION body that took the "true" edge only: the untaken false-branch
    # node must NOT be required for the iteration to be done.
    wf = _wf([
        _node("loop", NodeType.LOOP),
        _node("cond", NodeType.CONDITION),
        _node("t"), _node("f"),
    ])
    dag = _dag({
        "loop": [WorkflowEdge(source_node_id="loop", target_node_id="cond", condition="body")],
        "cond": [
            WorkflowEdge(id="e_true", source_node_id="cond", target_node_id="t", source_port_id="true_branch"),
            WorkflowEdge(id="e_false", source_node_id="cond", target_node_id="f", source_port_id="false_branch"),
        ],
    })
    # cond completed having taken only e_true; iter_progress has cond + t.
    run = _run("loop", ["cond", "t"], last_taken_edge_ports={"cond": {"e_true": "true_branch"}})
    assert run.decide_loop_iteration("loop", 0, wf, dag) is True
