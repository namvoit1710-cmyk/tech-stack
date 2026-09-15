"""Unit tests for the Phase 5.2.2 runtime body-subgraph helpers.

Covers the module-level helpers added to
``app.layer2_application.orchestration.loop_driver.event_driven_loop_driver``:

- ``_find_body_entry_node``: finds the loop's body-entry node via the
  "body" edge or the first non-exit outgoing edge.
- ``_post_loop_reachable``: BFS from the exit-edge target(s) to
  compute the post-loop node set; the body BFS prunes against this.
- ``iterate_body_subgraph``: BFS from body-entry, terminating at
  LOOP_EXIT / back-edges / exit edges / post-loop-reachable nodes.
  Used by ``orchestrate_loop_iteration``'s iter-boundary cleanup.
- ``is_iteration_done``: checks that every body-subgraph node is
  in ``loop_state["iter_progress"][iter_idx]``. Used by
  ``OrchestrationEngine.maybe_enqueue_loop_iteration`` to decide when
  to enqueue a ``LoopIterationAction``.

These helpers replaced the original plan's TopologyBuilder body_subgraph
precompute — they run at runtime using the existing ``topology.edge_map``
and ``run.context.loop_state["iter_progress"]``.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.value_objects.node_type import NodeType


# ──────────────────────────── helpers ─────────────────────────────────


def _dag(edge_map):
    return GraphTopology(edge_map=edge_map, adjacency={}, predecessors={}, entry_nodes=[], leaf_nodes=[])


def _wf(nodes: list[WorkflowNode]) -> Workflow:
    return Workflow(id="wf", nodes=nodes, edges=[])


# ──────────────────── _is_loop_exit_edge ────────────────────────────


class TestIsLoopExitEdge:
    def test_exit_condition(self):
        assert WorkflowEdge(source_node_id="l", target_node_id="t", condition="exit").is_loop_exit()

    def test_complete_condition(self):
        assert WorkflowEdge(source_node_id="l", target_node_id="t", condition="complete").is_loop_exit()

    def test_loop_complete_port(self):
        assert WorkflowEdge(source_node_id="l", target_node_id="t", source_port_id="loop_complete").is_loop_exit()

    def test_body_edge_is_not_exit(self):
        assert not WorkflowEdge(source_node_id="l", target_node_id="t", condition="body").is_loop_exit()

    def test_empty_edge_is_not_exit(self):
        assert not WorkflowEdge(source_node_id="l", target_node_id="t").is_loop_exit()


# ─────────────────── _find_body_entry_node ──────────────────────────


class TestFindBodyEntryNode:
    def test_explicit_body_edge(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_n = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, body, exit_n])
        edges = [
            WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body"),
            WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
        ]
        dag = _dag({"loop": edges})
        result = dag._body_entry_node("loop", wf)
        assert result is not None and result.id == "body"

    def test_fallback_first_non_exit(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        b = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        e = WorkflowNode(id="e", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, b, e])
        edges = [
            # No explicit body edge; first non-exit is "b"
            WorkflowEdge(source_node_id="loop", target_node_id="b"),
            WorkflowEdge(source_node_id="loop", target_node_id="e", condition="exit"),
        ]
        dag = _dag({"loop": edges})
        result = dag._body_entry_node("loop", wf)
        assert result is not None and result.id == "b"

    def test_no_outgoing_edges(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        wf = _wf([loop])
        dag = _dag({})
        assert dag._body_entry_node("loop", wf) is None


# ─────────────────── _post_loop_reachable ──────────────────────────


class TestPostLoopReachable:
    def test_empty_when_no_exit_edge(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, body])
        dag = _dag({"loop": [WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body")]})
        assert dag._post_loop_reachable("loop") == frozenset()

    def test_single_post_loop_node(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        after = WorkflowNode(id="after", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, after])
        dag = _dag({"loop": [WorkflowEdge(source_node_id="loop", target_node_id="after", condition="exit")]})
        assert dag._post_loop_reachable("loop") == frozenset({"after"})

    def test_transitive_post_loop_chain(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        a = WorkflowNode(id="a", node_type=NodeType.TASK, worker_type="w")
        b = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        c = WorkflowNode(id="c", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, a, b, c])
        edges = {
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="a", condition="exit")],
            "a": [WorkflowEdge(source_node_id="a", target_node_id="b")],
            "b": [WorkflowEdge(source_node_id="b", target_node_id="c")],
        }
        dag = _dag(edges)
        result = dag._post_loop_reachable("loop")
        assert result == frozenset({"a", "b", "c"})


# ──────────────────── iterate_body_subgraph ────────────────────────


class TestIterateBodySubgraph:
    def test_single_node_body(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_n = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, body, exit_n])
        edges = {
            "loop": [
                WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body"),
                WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
            ]
        }
        dag = _dag(edges)
        result = dag.body_subgraph("loop", wf)
        assert set(result) == {"body"}

    def test_multi_node_body_linear(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        a = WorkflowNode(id="a", node_type=NodeType.TASK, worker_type="w")
        b = WorkflowNode(id="b", node_type=NodeType.TASK, worker_type="w")
        c = WorkflowNode(id="c", node_type=NodeType.TASK, worker_type="w")
        exit_n = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, a, b, c, exit_n])
        edges = {
            "loop": [
                WorkflowEdge(source_node_id="loop", target_node_id="a", condition="body"),
                WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
            ],
            "a": [WorkflowEdge(source_node_id="a", target_node_id="b")],
            "b": [WorkflowEdge(source_node_id="b", target_node_id="c")],
        }
        dag = _dag(edges)
        result = set(dag.body_subgraph("loop", wf))
        assert result == {"a", "b", "c"}

    def test_multi_node_body_with_diamond_fan_in(self):
        """The user's example: CONDITION → (true → HTTP) / (false →
        COMPUTE → HTTP). The body is {CONDITION, COMPUTE, HTTP}.
        """
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        cond = WorkflowNode(id="cond", node_type=NodeType.CONDITION)
        compute = WorkflowNode(id="compute", node_type=NodeType.COMPUTE)
        http = WorkflowNode(id="http", node_type=NodeType.TASK, worker_type="w")
        exit_n = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, cond, compute, http, exit_n])
        edges = {
            "loop": [
                WorkflowEdge(source_node_id="loop", target_node_id="cond", condition="body"),
                WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
            ],
            "cond": [
                WorkflowEdge(source_node_id="cond", target_node_id="http", source_port_id="true_branch"),
                WorkflowEdge(source_node_id="cond", target_node_id="compute", source_port_id="false_branch"),
            ],
            "compute": [WorkflowEdge(source_node_id="compute", target_node_id="http")],
        }
        dag = _dag(edges)
        result = set(dag.body_subgraph("loop", wf))
        assert result == {"cond", "compute", "http"}

    def test_loop_exit_terminates_body(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        cond = WorkflowNode(id="cond", node_type=NodeType.CONDITION)
        brk = WorkflowNode(id="brk", node_type=NodeType.LOOP_EXIT)
        next_n = WorkflowNode(id="next", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, cond, brk, next_n])
        edges = {
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="cond", condition="body")],
            "cond": [
                WorkflowEdge(source_node_id="cond", target_node_id="brk", source_port_id="true_branch"),
                WorkflowEdge(source_node_id="cond", target_node_id="next", source_port_id="false_branch"),
            ],
            # If LOOP_EXIT accidentally had outgoing edges, the BFS
            # should STILL not follow them — LOOP_EXIT is dead-end.
            "brk": [WorkflowEdge(source_node_id="brk", target_node_id="next")],
        }
        dag = _dag(edges)
        result = set(dag.body_subgraph("loop", wf))
        # brk is included (we visit it), but its outgoing edge is
        # NOT followed, so "next" is only added because it's
        # reachable from cond's false branch.
        assert "brk" in result
        assert "cond" in result
        assert "next" in result  # still reachable via cond→next

    def test_post_loop_contamination_guard(self):
        """Anti-pattern: body node has a stray edge to a post-loop
        node. BFS should NOT include the post-loop node because
        _post_loop_reachable marks it."""
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        summary = WorkflowNode(id="summary", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, body, summary])
        edges = {
            "loop": [
                WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body"),
                WorkflowEdge(source_node_id="loop", target_node_id="summary", condition="exit"),
            ],
            # Anti-pattern: body has an edge to summary (post-loop)
            "body": [WorkflowEdge(source_node_id="body", target_node_id="summary")],
        }
        dag = _dag(edges)
        result = set(dag.body_subgraph("loop", wf))
        assert result == {"body"}  # summary excluded


# ────────────────────── is_iteration_done ──────────────────────────


class TestIsIterationDone:
    @staticmethod
    def _build_run_with_loop_state(
        loop_node_id: str,
        iter_progress_list: list[str],
        iter_idx: int = 0,
    ) -> WorkflowRun:
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
            ),
        )

    def _simple_body_workflow(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        body = WorkflowNode(id="body", node_type=NodeType.TASK, worker_type="w")
        exit_n = WorkflowNode(id="exit", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, body, exit_n])
        edges = {
            "loop": [
                WorkflowEdge(source_node_id="loop", target_node_id="body", condition="body"),
                WorkflowEdge(source_node_id="loop", target_node_id="exit", condition="exit"),
            ]
        }
        dag = _dag(edges)
        return wf, dag

    def test_single_node_body_done(self):
        wf, dag = self._simple_body_workflow()
        run = self._build_run_with_loop_state("loop", ["body"])
        assert dag.iteration_done(run, "loop", 0, wf) is True

    def test_single_node_body_not_yet_done(self):
        wf, dag = self._simple_body_workflow()
        run = self._build_run_with_loop_state("loop", [])
        assert dag.iteration_done(run, "loop", 0, wf) is False

    def test_multi_node_body_all_complete(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        a = WorkflowNode(id="a", node_type=NodeType.COMPUTE)
        b = WorkflowNode(id="b", node_type=NodeType.COMPUTE)
        wf = _wf([loop, a, b])
        dag = _dag({
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="a", condition="body")],
            "a": [WorkflowEdge(source_node_id="a", target_node_id="b")],
        })
        run = self._build_run_with_loop_state("loop", ["a", "b"])
        assert dag.iteration_done(run, "loop", 0, wf) is True

    def test_multi_node_body_missing_leaf(self):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        a = WorkflowNode(id="a", node_type=NodeType.COMPUTE)
        b = WorkflowNode(id="b", node_type=NodeType.COMPUTE)
        wf = _wf([loop, a, b])
        dag = _dag({
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="a", condition="body")],
            "a": [WorkflowEdge(source_node_id="a", target_node_id="b")],
        })
        # 'a' done but 'b' not
        run = self._build_run_with_loop_state("loop", ["a"])
        assert dag.iteration_done(run, "loop", 0, wf) is False

    def test_diamond_fan_in_both_branches_complete(self):
        """User's example: CONDITION → HTTP (true) / COMPUTE → HTTP
        (false). When the true branch ran, iter_progress = {CONDITION,
        HTTP, COMPUTE-cascade-skipped}. The BFS reaches all three nodes
        and they're all tracked → done.
        """
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        cond = WorkflowNode(id="cond", node_type=NodeType.CONDITION)
        compute = WorkflowNode(id="compute", node_type=NodeType.COMPUTE)
        http = WorkflowNode(id="http", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, cond, compute, http])
        dag = _dag({
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="cond", condition="body")],
            "cond": [
                WorkflowEdge(source_node_id="cond", target_node_id="http", source_port_id="true_branch"),
                WorkflowEdge(source_node_id="cond", target_node_id="compute", source_port_id="false_branch"),
            ],
            "compute": [WorkflowEdge(source_node_id="compute", target_node_id="http")],
        })
        # True branch path — COMPUTE was cascade-skipped and the hook
        # swept it into iter_progress along with CONDITION and HTTP.
        run = self._build_run_with_loop_state("loop", ["cond", "http", "compute"])
        assert dag.iteration_done(run, "loop", 0, wf) is True

    def test_diamond_fan_in_http_missing(self):
        """Partial progress: CONDITION + COMPUTE done but HTTP hasn't
        run yet. Iteration NOT done."""
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        cond = WorkflowNode(id="cond", node_type=NodeType.CONDITION)
        compute = WorkflowNode(id="compute", node_type=NodeType.COMPUTE)
        http = WorkflowNode(id="http", node_type=NodeType.TASK, worker_type="w")
        wf = _wf([loop, cond, compute, http])
        dag = _dag({
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="cond", condition="body")],
            "cond": [
                WorkflowEdge(source_node_id="cond", target_node_id="http", source_port_id="true_branch"),
                WorkflowEdge(source_node_id="cond", target_node_id="compute", source_port_id="false_branch"),
            ],
            "compute": [WorkflowEdge(source_node_id="compute", target_node_id="http")],
        })
        run = self._build_run_with_loop_state("loop", ["cond", "compute"])
        assert dag.iteration_done(run, "loop", 0, wf) is False

    def test_no_loop_state_returns_false(self):
        wf, dag = self._simple_body_workflow()
        run = WorkflowRun(id="run-1", workflow_id="wf", context=RunContext())
        assert dag.iteration_done(run, "loop", 0, wf) is False

    def test_empty_body_returns_true(self):
        """A loop with no body-entry (degenerate) is trivially done."""
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        wf = _wf([loop])
        dag = _dag({})
        run = self._build_run_with_loop_state("loop", [])
        assert dag.iteration_done(run, "loop", 0, wf) is True


class TestIsIterationDoneTakenEdgeRouting:
    """Cascade-skip retirement follow-up: ``is_iteration_done`` must not
    require nodes that are only reachable through a branch the CONDITION /
    SWITCH did not take this iteration (the untaken branch never runs and
    nothing marks it skipped anymore). Branching nodes are expanded only
    along their ``last_taken_edge_ports`` mirror entry."""

    @staticmethod
    def _condition_body(true_edge_id="e-true", false_edge_id="e-false"):
        loop = WorkflowNode(id="loop", node_type=NodeType.LOOP)
        cond = WorkflowNode(id="cond", node_type=NodeType.CONDITION)
        t = WorkflowNode(id="t", node_type=NodeType.COMPUTE)
        f = WorkflowNode(id="f", node_type=NodeType.COMPUTE)
        wf = _wf([loop, cond, t, f])
        dag = _dag({
            "loop": [WorkflowEdge(source_node_id="loop", target_node_id="cond", condition="body")],
            "cond": [
                WorkflowEdge(
                    id=true_edge_id, source_node_id="cond",
                    target_node_id="t", source_port_id="true_branch",
                ),
                WorkflowEdge(
                    id=false_edge_id, source_node_id="cond",
                    target_node_id="f", source_port_id="false_branch",
                ),
            ],
        })
        return wf, dag

    @staticmethod
    def _run(progress: list[str], taken: dict[str, dict[str, str]]) -> WorkflowRun:
        return WorkflowRun(
            id="run-1", workflow_id="wf",
            context=RunContext(
                loop_state={
                    "loop": {
                        "iter_progress": {0: list(progress)},
                        "total": 2,
                        "completed_iterations": [],
                    },
                },
                last_taken_edge_ports=taken,
            ),
        )

    def test_untaken_branch_not_required(self):
        """cond took the true branch; t ran. f must NOT block the iteration."""
        wf, dag = self._condition_body()
        run = self._run(
            ["cond", "t"],
            {"cond": {"e-true": "true_branch"}},
        )
        assert dag.iteration_done(run, "loop", 0, wf) is True

    def test_taken_branch_still_required(self):
        """cond took the true branch but t hasn't run — iteration NOT done."""
        wf, dag = self._condition_body()
        run = self._run(
            ["cond"],
            {"cond": {"e-true": "true_branch"}},
        )
        assert dag.iteration_done(run, "loop", 0, wf) is False

    def test_missing_mirror_falls_back_to_all_edges(self):
        """No mirror entry → conservative: every branch is still required
        (stall is recoverable; premature advance corrupts loop state)."""
        wf, dag = self._condition_body()
        run = self._run(["cond", "t"], {})
        assert dag.iteration_done(run, "loop", 0, wf) is False
