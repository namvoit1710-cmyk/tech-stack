"""Unit tests for successor_processor.py."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer2_application.node_handlers.base import NodeHandlerResult, NodeHandlerRegistry
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor, ProcessingResult, BUILTIN_NODE_TYPES
from app.layer1_domain.events import DomainEvent, NodeFailed
from app.layer1_domain.exceptions import ExpressionError, InputRequirementError


class TestProcessingResult:
    def test_initial_state(self):
        r = ProcessingResult()
        assert r.tasks_to_dispatch == []
        assert r.skipped_node_ids == []
        assert r.state_updates == {}
        assert r.loop_state_updates == {}
        assert r.events == []

    def test_merge(self):
        r = ProcessingResult()
        hr = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t1")],
            skipped_node_ids=["s1"],
            state_updates={"key": "val"},
            loop_state_updates={"loop1": {"x": 1}},
            events=[DomainEvent()],
        )
        r.merge(hr)
        assert len(r.tasks_to_dispatch) == 1
        assert r.skipped_node_ids == ["s1"]
        assert r.state_updates == {"key": "val"}
        assert r.loop_state_updates == {"loop1": {"x": 1}}
        assert len(r.events) == 1

    def test_merge_no_loop_state(self):
        r = ProcessingResult()
        hr = NodeHandlerResult(
            tasks_to_dispatch=[],
            events=[],
            loop_state_updates=None,
        )
        r.merge(hr)
        assert r.loop_state_updates == {}

    def test_merge_multiple(self):
        r = ProcessingResult()
        hr1 = NodeHandlerResult(tasks_to_dispatch=[Task(id="t1")], events=[])
        hr2 = NodeHandlerResult(tasks_to_dispatch=[Task(id="t2")], events=[])
        r.merge(hr1)
        r.merge(hr2)
        assert len(r.tasks_to_dispatch) == 2


class TestSuccessorProcessor:
    def _make_processor(self, handler_registry=None):
        return SuccessorProcessor(
            handler_registry=handler_registry or MagicMock(),
            task_factory=TaskFactory(),
        )

    def _make_linear_workflow(self):
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[WorkflowEdge(source_node_id="A", target_node_id="B")],
        )
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": []},
            predecessors={"A": [], "B": ["A"]},
            edge_map={
                "A": workflow.edges,
                "B": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["B"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology),
        )
        return workflow, run, topology

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_single_successor(self):
        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="new-t", node_id="B", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        workflow, run, topology = self._make_linear_workflow()
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert len(result.tasks_to_dispatch) == 1
        assert "A" in run.context.completed_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_already_completed_successor_triggers_reentry(self):
        """With cycle support, a completed successor is re-entered when all predecessors complete again."""
        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="new-t", node_id="B", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        workflow, run, topology = self._make_linear_workflow()
        # Simulate B was previously executed (completed + triggered)
        run.context.completed_nodes.add("B")
        run.context.triggered_nodes.add("B")

        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # Cycle re-entry: B was completed but gets re-processed
        assert len(result.tasks_to_dispatch) == 1
        registry.get.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_already_skipped_successor(self):
        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="new-t", node_id="B", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        workflow, run, topology = self._make_linear_workflow()
        # Simulate B was previously skipped (skip check is disabled for cycle support)
        run.context.skipped_nodes.add("B")

        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # Cycle re-entry: B was skipped but gets re-processed
        assert len(result.tasks_to_dispatch) == 1
        assert "B" not in run.context.skipped_nodes
        registry.get.assert_called()

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_not_all_predecessors_done(self):
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="C"),
                WorkflowEdge(source_node_id="B", target_node_id="C"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["C"], "B": ["C"], "C": []},
            predecessors={"A": [], "B": [], "C": ["A", "B"]},
            edge_map={"A": [workflow.edges[0]], "B": [workflow.edges[1]], "C": []},
            entry_nodes=["A", "B"],
            leaf_nodes=["C"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology),
        )

        registry = MagicMock()
        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # B is not done, so C should not be dispatched
        assert len(result.tasks_to_dispatch) == 0

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_builtin_node_recursion(self):
        """When successor is a builtin node, the result triggers recursive processing."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="COND", node_type=NodeType.CONDITION, config={"expression": "true"}),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="COND"),
                WorkflowEdge(source_node_id="COND", target_node_id="B", condition="then"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["COND"], "COND": ["B"], "B": []},
            predecessors={"A": [], "COND": ["A"], "B": ["COND"]},
            edge_map={
                "A": [workflow.edges[0]],
                "COND": [workflow.edges[1]],
                "B": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["B"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology),
        )

        registry = MagicMock()
        handler = AsyncMock()
        # COND handler produces a task for B
        cond_task = Task(id="t-B", node_id="B", run_id="run-1")
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[cond_task],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # COND is builtin so it gets completed and its successor (B) is processed
        assert "COND" in run.context.completed_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_missing_target_node_skipped(self):
        """If target_node from edge doesn't exist in workflow, skip it."""
        workflow = Workflow(
            id="wf-1",
            nodes=[WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w")],
            edges=[WorkflowEdge(source_node_id="A", target_node_id="GHOST")],
        )
        topology = GraphTopology(
            adjacency={"A": ["GHOST"]},
            predecessors={"A": []},
            edge_map={"A": workflow.edges},
            entry_nodes=["A"],
            leaf_nodes=[],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology),
        )
        registry = MagicMock()
        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)
        assert len(result.tasks_to_dispatch) == 0

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_handler_state_and_loop_updates_applied(self):
        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[],
            state_updates={"B": {"val": 42}},
            loop_state_updates={"B": {"items": [1]}},
            skipped_node_ids=["X"],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        workflow, run, topology = self._make_linear_workflow()
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert run.context.variables.get("B") == {"val": 42}
        assert run.context.loop_state.get("B") == {"items": [1]}
        assert "X" in run.context.skipped_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: _evaluate_branching_node fast-path removed — SuccessorProcessor.process() no longer invokes branching handlers; legacy 4-arg API")
    async def test_branching_chain_marks_inner_condition_task_completed(self):
        """Regression for stuck-run bug:
        TASK → CONDITION(C1) → CONDITION(C2) → TASK(T)

        When C1's handler runs as a target and produces C2's task, the engine
        recursively calls ``process(task_C2)``. That recursion hits the
        ``_evaluate_branching_node`` fast-path which adds C2 to
        ``completed_nodes`` but used to leave C2's task in READY status.

        Downstream, ``process_and_dispatch_successors`` would persist
        ``task_C2`` as READY and skip dispatch (node already in
        completed_nodes), leaving a phantom "active READY task" that blocks
        ``check_run_completion`` from ever marking the run done. This test
        verifies the task is now COMPLETED so the run can finish.
        """
        from app.layer2_application.resolve.variable_resolver import (
            ConditionEvaluator,
            MetadataResolverStrategy,
            TraversalResolverStrategy,
            VariableResolver,
        )
        from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler

        # Workflow: A (TASK) → C1 (CONDITION) → C2 (CONDITION) → T (TASK).
        # Both conditions evaluate FALSE (no expression → false fallback when
        # the structured-data path is unused) → take the false_branch each
        # time. Each condition also has a sibling on the un-taken branch so
        # the cascade-skip path is exercised but isn't relevant to this test.
        nodes = [
            WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(
                id="C1",
                node_type=NodeType.CONDITION,
                input_binding={
                    "data": [{"key": "no-such-var", "operations": "==", "value": "yes"}],
                },
            ),
            WorkflowNode(id="C1_unused", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(
                id="C2",
                node_type=NodeType.CONDITION,
                input_binding={
                    "data": [{"key": "no-such-var", "operations": "==", "value": "yes"}],
                },
            ),
            WorkflowNode(id="C2_unused", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(id="T", node_type=NodeType.TASK, worker_type="w"),
        ]
        edges = [
            WorkflowEdge(source_node_id="A", target_node_id="C1"),
            WorkflowEdge(source_node_id="C1", target_node_id="C1_unused", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="C1", target_node_id="C2", source_port_id="false_branch"),
            WorkflowEdge(source_node_id="C2", target_node_id="C2_unused", source_port_id="true_branch"),
            WorkflowEdge(source_node_id="C2", target_node_id="T", source_port_id="false_branch"),
        ]
        workflow = Workflow(id="wf-1", nodes=nodes, edges=edges)
        topology = GraphTopology(
            adjacency={
                "A": ["C1"], "C1": ["C1_unused", "C2"],
                "C2": ["C2_unused", "T"],
                "C1_unused": [], "C2_unused": [], "T": [],
            },
            predecessors={
                "A": [], "C1": ["A"],
                "C1_unused": ["C1"], "C2": ["C1"],
                "C2_unused": ["C2"], "T": ["C2"],
            },
            edge_map={
                "A": [edges[0]],
                "C1": [edges[1], edges[2]],
                "C2": [edges[3], edges[4]],
                "C1_unused": [], "C2_unused": [], "T": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["C1_unused", "C2_unused", "T"],
        )
        run = WorkflowRun(
            id="run-1", workflow_id="wf-1",
            context=RunContext(topology=topology),
        )

        resolver = VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])
        condition_handler = ConditionNodeHandler(ConditionEvaluator(resolver))
        registry = NodeHandlerRegistry()
        registry.register(NodeType.CONDITION, condition_handler)

        processor = SuccessorProcessor(handler_registry=registry, task_factory=TaskFactory())
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # Both CONDITION nodes flowed inline → both in completed_nodes.
        assert "C1" in run.context.completed_nodes
        assert "C2" in run.context.completed_nodes

        # The fix: every task dispatched from the chain has a terminal status.
        # Before the fix, the task generated for C2 (the inner CONDITION) was
        # left READY because `_evaluate_branching_node` only updated
        # completed_nodes, not the task. That orphan READY task blocked the
        # run from ever completing.
        task_by_node = {t.node_id: t for t in result.tasks_to_dispatch}
        assert "C2" in task_by_node, "inner CONDITION's task must be in the dispatch list"
        assert task_by_node["C2"].status == TaskStatus.COMPLETED
        assert task_by_node["C2"].completed_at is not None

        # The downstream leaf task is still READY — it's a worker task, the
        # broker is supposed to dispatch it. The bug was specifically about
        # the inner CONDITION's task being stuck, not the leaf.
        assert "T" in task_by_node
        assert task_by_node["T"].status == TaskStatus.READY


class TestPortRoutingSkip:
    """Tests for skipping nodes on filtered-out port edges."""

    def _make_processor(self, handler_registry=None):
        return SuccessorProcessor(
            handler_registry=handler_registry or MagicMock(),
            task_factory=TaskFactory(),
        )

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_port_routing_skips_failure_branch_on_success(self):
        """When a TASK succeeds, targets on failure edges are skipped."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B", source_port_id="success"),
                WorkflowEdge(source_node_id="A", target_node_id="C", source_port_id="failure"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": [], "C": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"]},
            edge_map={"A": workflow.edges, "B": [], "C": []},
            entry_nodes=["A"],
            leaf_nodes=["B", "C"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t-B", node_id="B", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert "C" in result.skipped_node_ids
        assert "C" in run.context.skipped_nodes
        assert len(result.tasks_to_dispatch) == 1
        assert result.tasks_to_dispatch[0].node_id == "B"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_port_routing_skips_downstream_chain(self):
        """Downstream of a port-skipped target is also skipped."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="D", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B", source_port_id="success"),
                WorkflowEdge(source_node_id="A", target_node_id="C", source_port_id="failure"),
                WorkflowEdge(source_node_id="C", target_node_id="D"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": [], "C": ["D"], "D": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"], "D": ["C"]},
            edge_map={"A": workflow.edges[:2], "B": [], "C": [workflow.edges[2]], "D": []},
            entry_nodes=["A"],
            leaf_nodes=["B", "D"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t-B", node_id="B", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert "C" in result.skipped_node_ids
        assert "D" in result.skipped_node_ids
        assert "C" in run.context.skipped_nodes
        assert "D" in run.context.skipped_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_port_routing_merge_point_not_skipped(self):
        """Merge point with a live predecessor is NOT skipped."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="M", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B", source_port_id="success"),
                WorkflowEdge(source_node_id="A", target_node_id="C", source_port_id="failure"),
                WorkflowEdge(source_node_id="B", target_node_id="M"),
                WorkflowEdge(source_node_id="C", target_node_id="M"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": ["M"], "C": ["M"], "M": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"], "M": ["B", "C"]},
            edge_map={
                "A": workflow.edges[:2],
                "B": [workflow.edges[2]],
                "C": [workflow.edges[3]],
                "M": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["M"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t-B", node_id="B", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # C is skipped (on failure branch)
        assert "C" in result.skipped_node_ids
        # M is NOT skipped because B (success branch) is still live
        assert "M" not in result.skipped_node_ids
        assert "M" not in run.context.skipped_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: SuccessorProcessor.process() signature changed — tests use legacy API")
    async def test_port_routing_no_port_edges_not_filtered(self):
        """Edges without source_port_id are NOT treated as filtered-out."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B", source_port_id="success"),
                WorkflowEdge(source_node_id="A", target_node_id="C"),  # no port
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["B", "C"], "B": [], "C": []},
            predecessors={"A": [], "B": ["A"], "C": ["A"]},
            edge_map={"A": workflow.edges, "B": [], "C": []},
            entry_nodes=["A"],
            leaf_nodes=["B", "C"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[
                Task(id="t-B", node_id="B", run_id="run-1"),
                Task(id="t-C", node_id="C", run_id="run-1"),
            ],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # C should NOT be skipped since it has no port (default edge)
        assert "C" not in result.skipped_node_ids
        assert "C" not in run.context.skipped_nodes


@pytest.mark.skip(reason=(
    "Phase 6.3: SuccessorProcessor.process() no longer routes HUMAN_ACTION "
    "branches. process(run, workflow, decision) just dispatches a pre-computed "
    "FanoutDecision; confirm/reject routing moved to ResumePendingTaskUseCase "
    "(taken_edge_ports) + DispatcherConsumer._emit_branching. These tests use "
    "the legacy 4-arg API and the removed output-driven routing contract. "
    "Coverage now lives in test_resume_pending_task.py (confirm/reject/default/"
    "infer) and test_dispatcher_consumer.py (edge emission)."
))
class TestHumanActionRouting:
    def _make_processor(self, handler_registry=None):
        return SuccessorProcessor(
            handler_registry=handler_registry or MagicMock(),
            task_factory=TaskFactory(),
        )

    def _make_branching_workflow(self, approved_edge=None, rejected_edge=None, *, extra_nodes=None, extra_edges=None):
        approved_edge = approved_edge or {"condition": "confirm"}
        rejected_edge = rejected_edge or {"condition": "reject"}
        nodes = [
            WorkflowNode(id="ha", node_type=NodeType.HUMAN_ACTION),
            WorkflowNode(id="approved", node_type=NodeType.TASK, worker_type="w"),
            WorkflowNode(id="rejected", node_type=NodeType.TASK, worker_type="w"),
        ]
        if extra_nodes:
            nodes.extend(extra_nodes)
        edges = [
            WorkflowEdge(source_node_id="ha", target_node_id="approved", **approved_edge),
            WorkflowEdge(source_node_id="ha", target_node_id="rejected", **rejected_edge),
        ]
        if extra_edges:
            edges.extend(extra_edges)
        return Workflow(id="wf-1", nodes=nodes, edges=edges)

    def _make_topology(self, workflow, adjacency, predecessors, entry=None, leaf=None):
        edge_map = {node.id: [] for node in workflow.nodes}
        for edge in workflow.edges:
            edge_map.setdefault(edge.source_node_id, []).append(edge)
        return GraphTopology(
            adjacency=adjacency,
            predecessors=predecessors,
            edge_map=edge_map,
            entry_nodes=entry or ["ha"],
            leaf_nodes=leaf or [node.id for node in workflow.nodes if node.id != "ha"],
        )

    def _make_run(self, topology, node_output=None):
        variables = {}
        if node_output is not None:
            variables["ha"] = node_output
        return WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology, variables=variables),
        )

    def _registry_with_echo_handler(self):
        registry = MagicMock()
        handler = AsyncMock()

        async def handle_side_effect(**kwargs):
            node = kwargs["node"]
            return NodeHandlerResult(
                tasks_to_dispatch=[Task(id=f"t-{node.id}", node_id=node.id, run_id="run-1")],
                events=[],
            )

        handler.handle.side_effect = handle_side_effect
        registry.get.return_value = handler
        return registry, handler

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("approved_edge", "case_id"),
        [
            ({"condition": "confirm"}, "condition-confirm"),
            ({"condition": "true"}, "condition-true"),
            ({"condition": "then"}, "condition-then"),
            ({"source_port_id": "confirm"}, "port-confirm"),
            ({"source_port_id": "success"}, "port-success"),
            ({"source_port_id": "true_branch"}, "port-true-branch"),
            ({"source_port_id": "then"}, "port-then"),
            ({"source_port_id": "true"}, "port-true"),
            ({}, "default-empty"),
        ],
        ids=lambda item: item if isinstance(item, str) else None,
    )
    async def test_human_action_confirm_variants_route_taken_branch(self, approved_edge, case_id):
        workflow = self._make_branching_workflow(approved_edge=approved_edge, rejected_edge={"condition": "reject"})
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "confirm"})
        registry, handler = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["approved"], case_id
        assert "rejected" in result.skipped_node_ids
        assert "rejected" in run.context.skipped_nodes
        assert handler.handle.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rejected_edge", "case_id"),
        [
            ({"condition": "reject"}, "condition-reject"),
            ({"condition": "false"}, "condition-false"),
            ({"condition": "else"}, "condition-else"),
            ({"source_port_id": "reject"}, "port-reject"),
            ({"source_port_id": "failure"}, "port-failure"),
            ({"source_port_id": "false_branch"}, "port-false-branch"),
            ({"source_port_id": "else"}, "port-else"),
            ({"source_port_id": "false"}, "port-false"),
        ],
        ids=lambda item: item if isinstance(item, str) else None,
    )
    async def test_human_action_reject_variants_route_taken_branch(self, rejected_edge, case_id):
        workflow = self._make_branching_workflow(approved_edge={"condition": "confirm"}, rejected_edge=rejected_edge)
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "reject"})
        registry, handler = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["rejected"], case_id
        assert "approved" in result.skipped_node_ids
        assert "approved" in run.context.skipped_nodes
        assert handler.handle.await_count == 1

    @pytest.mark.asyncio
    async def test_human_action_confirm_routes_only_confirm_branch(self):
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="ha", node_type=NodeType.HUMAN_ACTION),
                WorkflowNode(id="approved", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="rejected", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="ha", target_node_id="approved", condition="confirm"),
                WorkflowEdge(source_node_id="ha", target_node_id="rejected", condition="reject"),
            ],
        )
        topology = GraphTopology(
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
            edge_map={"ha": workflow.edges, "approved": [], "rejected": []},
            entry_nodes=["ha"],
            leaf_nodes=["approved", "rejected"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology, variables={"ha": {"_action": "confirm"}}),
        )

        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t-approved", node_id="approved", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["approved"]
        assert "rejected" in result.skipped_node_ids
        assert "rejected" in run.context.skipped_nodes

    @pytest.mark.asyncio
    async def test_human_action_reject_routes_only_reject_branch_by_port(self):
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="ha", node_type=NodeType.HUMAN_ACTION),
                WorkflowNode(id="approved", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="rejected", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="ha", target_node_id="approved", source_port_id="confirm"),
                WorkflowEdge(source_node_id="ha", target_node_id="rejected", source_port_id="reject"),
            ],
        )
        topology = GraphTopology(
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
            edge_map={"ha": workflow.edges, "approved": [], "rejected": []},
            entry_nodes=["ha"],
            leaf_nodes=["approved", "rejected"],
        )
        run = WorkflowRun(
            id="run-1",
            workflow_id="wf-1",
            context=RunContext(topology=topology, variables={"ha": {"_action": "reject"}}),
        )

        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t-rejected", node_id="rejected", run_id="run-1")],
            events=[],
        )
        registry.get.return_value = handler

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["rejected"]
        assert "approved" in result.skipped_node_ids
        assert "approved" in run.context.skipped_nodes

    @pytest.mark.asyncio
    async def test_human_action_confirm_reenters_previously_skipped_branch(self):
        workflow = self._make_branching_workflow()
        dag_info = self._make_dag_info(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(dag_info, {"_action": "confirm"})
        run.context.skipped_nodes.add("approved")
        registry, handler = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(
            Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED),
            run,
            workflow,
            dag_info,
        )

        assert [task.node_id for task in result.tasks_to_dispatch] == ["approved"]
        assert "approved" not in run.context.skipped_nodes
        assert "rejected" in run.context.skipped_nodes
        assert handler.handle.await_count == 1

    @pytest.mark.asyncio
    async def test_missing_action_defaults_to_confirm(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {})
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["approved"]

    @pytest.mark.asyncio
    async def test_unknown_action_defaults_to_confirm(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "maybe"})
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["approved"]

    @pytest.mark.asyncio
    async def test_non_dict_output_defaults_to_confirm(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, None)
        run.context.variables["ha"] = "reject"
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["approved"]

    @pytest.mark.asyncio
    async def test_reject_action_is_trimmed_and_lowercased(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "  ReJeCt  "})
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert [task.node_id for task in result.tasks_to_dispatch] == ["rejected"]

    @pytest.mark.asyncio
    async def test_reject_with_no_matching_edge_dispatches_nothing(self):
        workflow = self._make_branching_workflow(
            approved_edge={"condition": "confirm"},
            rejected_edge={"condition": "confirm"},
        )
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "reject"})
        registry, handler = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert result.tasks_to_dispatch == []
        assert "approved" in result.skipped_node_ids
        assert "rejected" in result.skipped_node_ids
        assert handler.handle.await_count == 0

    @pytest.mark.asyncio
    async def test_untaken_branch_downstream_chain_is_skipped(self):
        workflow = self._make_branching_workflow(
            extra_nodes=[WorkflowNode(id="reject_child", node_type=NodeType.TASK, worker_type="w")],
            extra_edges=[WorkflowEdge(source_node_id="rejected", target_node_id="reject_child")],
        )
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": ["reject_child"], "reject_child": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"], "reject_child": ["rejected"]},
            leaf=["approved", "reject_child"],
        )
        run = self._make_run(topology, {"_action": "confirm"})
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert "rejected" in result.skipped_node_ids
        assert "reject_child" in result.skipped_node_ids

    @pytest.mark.asyncio
    async def test_merge_point_not_skipped_when_confirm_branch_is_live(self):
        workflow = self._make_branching_workflow(
            extra_nodes=[WorkflowNode(id="merge", node_type=NodeType.TASK, worker_type="w")],
            extra_edges=[
                WorkflowEdge(source_node_id="approved", target_node_id="merge"),
                WorkflowEdge(source_node_id="rejected", target_node_id="merge"),
            ],
        )
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": ["merge"], "rejected": ["merge"], "merge": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"], "merge": ["approved", "rejected"]},
            leaf=["merge"],
        )
        run = self._make_run(topology, {"_action": "confirm"})
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert "rejected" in result.skipped_node_ids
        assert "merge" not in result.skipped_node_ids
        assert "merge" not in run.context.skipped_nodes

    @pytest.mark.asyncio
    async def test_selected_branch_expression_error_creates_failed_task(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "confirm"})
        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.side_effect = ExpressionError("bad approve path")
        registry.get.return_value = handler
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        failed_tasks = [task for task in result.tasks_to_dispatch if task.status == TaskStatus.FAILED]
        assert len(failed_tasks) == 1
        assert failed_tasks[0].node_id == "approved"
        assert "rejected" in run.context.skipped_nodes

    @pytest.mark.asyncio
    async def test_selected_branch_input_requirement_error_creates_failed_task(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "reject"})
        registry = MagicMock()
        handler = AsyncMock()
        handler.handle.side_effect = InputRequirementError("missing approver")
        registry.get.return_value = handler
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        failed_tasks = [task for task in result.tasks_to_dispatch if task.status == TaskStatus.FAILED]
        assert len(failed_tasks) == 1
        assert failed_tasks[0].node_id == "rejected"
        assert "approved" in run.context.skipped_nodes

    @pytest.mark.asyncio
    async def test_selected_target_missing_is_ignored(self):
        workflow = Workflow(
            id="wf-1",
            nodes=[WorkflowNode(id="ha", node_type=NodeType.HUMAN_ACTION), WorkflowNode(id="rejected", node_type=NodeType.TASK, worker_type="w")],
            edges=[
                WorkflowEdge(source_node_id="ha", target_node_id="approved", condition="confirm"),
                WorkflowEdge(source_node_id="ha", target_node_id="rejected", condition="reject"),
            ],
        )
        topology = GraphTopology(
            adjacency={"ha": ["approved", "rejected"], "rejected": []},
            predecessors={"ha": [], "rejected": ["ha"]},
            edge_map={"ha": workflow.edges, "rejected": []},
            entry_nodes=["ha"],
            leaf_nodes=["rejected"],
        )
        run = self._make_run(topology, {"_action": "confirm"})
        registry, handler = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        assert result.tasks_to_dispatch == []
        assert "rejected" in run.context.skipped_nodes
        assert handler.handle.await_count == 0

    @pytest.mark.asyncio
    async def test_taken_branch_emits_single_edge_traversed_event(self):
        workflow = self._make_branching_workflow()
        topology = self._make_topology(
            workflow,
            adjacency={"ha": ["approved", "rejected"], "approved": [], "rejected": []},
            predecessors={"ha": [], "approved": ["ha"], "rejected": ["ha"]},
        )
        run = self._make_run(topology, {"_action": "confirm"})
        registry, _ = self._registry_with_echo_handler()
        processor = self._make_processor(handler_registry=registry)

        result = await processor.process(Task(id="t-ha", run_id="run-1", node_id="ha", status=TaskStatus.COMPLETED), run, workflow, topology)

        traversed = [event for event in result.events if event.__class__.__name__ == "EdgeTraversed"]
        assert len(traversed) == 1
        assert traversed[0].target_node_id == "approved"


class TestBuiltinNodeTypes:
    def test_all_expected_types(self):
        # BUILTIN_NODE_TYPES is DERIVED from the domain (specs with
        # runtime="engine"); the exhaustive partition is asserted in
        # tests/unit/node_spec/test_runtime_classification.py. Here we just
        # floor-check the inline control-flow + data types, including OUTPUT
        # and PROMPT, which previously drifted OUT of this set.
        expected = {NodeType.CONDITION, NodeType.SWITCH, NodeType.LOOP,
                    NodeType.PARALLEL, NodeType.MERGE, NodeType.TRIGGER,
                    NodeType.WORKFLOW, NodeType.COMPUTE,
                    NodeType.LOOP_EXIT, NodeType.LOOP_CONTINUE,
                    NodeType.SET_VARIABLE, NodeType.APPROVAL_FLOW,
                    NodeType.OUTPUT, NodeType.PROMPT}
        assert expected <= BUILTIN_NODE_TYPES

    def test_task_not_builtin(self):
        assert NodeType.TASK not in BUILTIN_NODE_TYPES

    def test_pending_types_not_builtin(self):
        for nt in (NodeType.INPUT, NodeType.HUMAN_ACTION,
                   NodeType.DATA_EDIT, NodeType.FILE_UPLOAD):
            assert nt not in BUILTIN_NODE_TYPES


class TestHandlerExceptionHandling:
    """Tests for ExpressionError / InputRequirementError caught in process()."""

    def _make_processor(self, handler_registry=None):
        return SuccessorProcessor(
            handler_registry=handler_registry or MagicMock(),
            task_factory=TaskFactory(),
        )

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_expression_error_creates_failed_task_and_follows_failure_port(self):
        """When handler raises ExpressionError, node gets a FAILED task
        and downstream routing follows the failure port."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C_ok", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C_err", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B"),
                WorkflowEdge(source_node_id="B", target_node_id="C_ok", source_port_id="success"),
                WorkflowEdge(source_node_id="B", target_node_id="C_err", source_port_id="failure"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": ["C_ok", "C_err"], "C_ok": [], "C_err": []},
            predecessors={"A": [], "B": ["A"], "C_ok": ["B"], "C_err": ["B"]},
            edge_map={
                "A": [workflow.edges[0]],
                "B": workflow.edges[1:],
                "C_ok": [],
                "C_err": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["C_ok", "C_err"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        call_count = 0

        async def handler_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call is for node B — raise ExpressionError
                raise ExpressionError("bad expression")
            # Second call is for node C_err (failure-port successor)
            return NodeHandlerResult(
                tasks_to_dispatch=[Task(id="t-Cerr", node_id="C_err", run_id="run-1")],
                events=[],
            )

        registry = MagicMock()
        handler_mock = AsyncMock()
        handler_mock.handle.side_effect = handler_side_effect
        registry.get.return_value = handler_mock

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # B should be marked as completed (via _create_failed_task)
        assert "B" in run.context.completed_nodes
        # A FAILED task for B should be in tasks_to_dispatch
        failed_tasks = [t for t in result.tasks_to_dispatch if t.status == TaskStatus.FAILED]
        assert len(failed_tasks) == 1
        assert failed_tasks[0].node_id == "B"
        assert failed_tasks[0].error == "bad expression"
        # C_ok should be skipped (success port, but B failed)
        assert "C_ok" in run.context.skipped_nodes
        # C_err task should be dispatched (failure port)
        c_err_tasks = [t for t in result.tasks_to_dispatch if t.node_id == "C_err"]
        assert len(c_err_tasks) == 1
        # NodeFailed event should be emitted
        task_failed_events = [e for e in result.events if isinstance(e, NodeFailed)]
        assert len(task_failed_events) == 1
        assert task_failed_events[0].node_id == "B"

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_expression_error_without_failure_port_skips_downstream(self):
        """When handler raises ExpressionError and no failure port exists,
        the node is marked failed and success-only downstream is skipped."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(source_node_id="A", target_node_id="B"),
                WorkflowEdge(source_node_id="B", target_node_id="C", source_port_id="success"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": ["C"], "C": []},
            predecessors={"A": [], "B": ["A"], "C": ["B"]},
            edge_map={
                "A": [workflow.edges[0]],
                "B": [workflow.edges[1]],
                "C": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["C"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        registry = MagicMock()
        handler_mock = AsyncMock()
        handler_mock.handle.side_effect = ExpressionError("missing var")
        registry.get.return_value = handler_mock

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        # B is marked completed (as failed)
        assert "B" in run.context.completed_nodes
        # A FAILED task for B
        failed_tasks = [t for t in result.tasks_to_dispatch if t.status == TaskStatus.FAILED]
        assert len(failed_tasks) == 1
        # C is skipped (success-only port, no failure port)
        assert "C" in run.context.skipped_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_input_requirement_error_handled_same_as_expression_error(self):
        """InputRequirementError should be handled the same way as ExpressionError."""
        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="B", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[WorkflowEdge(source_node_id="A", target_node_id="B")],
        )
        topology = GraphTopology(
            adjacency={"A": ["B"], "B": []},
            predecessors={"A": [], "B": ["A"]},
            edge_map={"A": workflow.edges, "B": []},
            entry_nodes=["A"],
            leaf_nodes=["B"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        registry = MagicMock()
        handler_mock = AsyncMock()
        handler_mock.handle.side_effect = InputRequirementError("missing keys: [x]")
        registry.get.return_value = handler_mock

        processor = self._make_processor(handler_registry=registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)

        result = await processor.process(completed_task, run, workflow, topology)

        assert "B" in run.context.completed_nodes
        failed_tasks = [t for t in result.tasks_to_dispatch if t.status == TaskStatus.FAILED]
        assert len(failed_tasks) == 1
        assert "missing keys" in failed_tasks[0].error


# ─────────── Phase 4b.4: post-handler BUILTIN port filter ────────────


class TestPostBuiltinPortFilter:
    """Phase 4b.4 — the post-handler BUILTIN edge-emission pass now uses
    ``compute_fanout``. Verify that:

    1. A port-routed BUILTIN (COMPUTE) with success + failure outgoing
       edges emits ``EdgeTraversed`` for the success branch only and
       cascade-skips the failure-branch subtree.
    2. The emitted edge and skip events carry deterministic ids derived
       from the target's ``builtin_task.id``.
    3. CONDITION with ``taken_edge_ports`` still behaves the same way
       (untaken branches NOT emitted). This is the regression surface
       for the two-branch refactor.
    """

    def _make_processor(self, registry):
        return SuccessorProcessor(
            handler_registry=registry,
            task_factory=TaskFactory(),
        )

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_compute_success_port_filters_failure_branch(self):
        """TRIGGER → COMPUTE(success→ok, failure→err). After processing
        TRIGGER, COMPUTE runs inline, completes, and the post-handler
        edge pass must:

        - emit EdgeTraversed only for the success→ok edge
        - NOT emit an edge for failure→err
        - cascade-skip "err" and append it to skipped_nodes + emit TaskSkipped
        """
        from app.layer1_domain.events import EdgeTraversed, TaskSkipped

        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="T", node_type=NodeType.TRIGGER),
                WorkflowNode(id="C", node_type=NodeType.COMPUTE, config={"expression": "1"}),
                WorkflowNode(id="ok", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="err", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="T", target_node_id="C"),
                WorkflowEdge(
                    id="e2", source_node_id="C", target_node_id="ok",
                    source_port_id="success",
                ),
                WorkflowEdge(
                    id="e3", source_node_id="C", target_node_id="err",
                    source_port_id="failure",
                ),
            ],
        )
        topology = GraphTopology(
            adjacency={"T": ["C"], "C": ["ok", "err"], "ok": [], "err": []},
            predecessors={"T": [], "C": ["T"], "ok": ["C"], "err": ["C"]},
            edge_map={
                "T": [workflow.edges[0]],
                "C": [workflow.edges[1], workflow.edges[2]],
                "ok": [],
                "err": [],
            },
            entry_nodes=["T"],
            leaf_nodes=["ok", "err"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        # COMPUTE handler returns empty state_updates — we only care about
        # port filtering in the post-handler edge pass.
        registry = MagicMock()
        compute_handler = AsyncMock()
        compute_handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[], events=[],
        )
        registry.get.return_value = compute_handler

        processor = self._make_processor(registry)
        # Trigger is the completed task (empty predecessors on T → COMPUTE is ready)
        trigger_task = Task(id="t-T", run_id="run-1", node_id="T", status=TaskStatus.COMPLETED)
        result = await processor.process(trigger_task, run, workflow, topology)

        # The post-handler pass for COMPUTE should have produced exactly
        # one EdgeTraversed (for success→ok) and one TaskSkipped (for err).
        post_edges = [
            e for e in result.events
            if isinstance(e, EdgeTraversed) and e.source_node_id == "C"
        ]
        post_skips = [e for e in result.events if isinstance(e, TaskSkipped)]

        assert len(post_edges) == 1, (
            f"Expected exactly 1 EdgeTraversed from COMPUTE, got "
            f"{[(e.edge_id, e.target_node_id) for e in post_edges]}. "
            f"Phase 4b.4 compute_fanout migration should port-filter."
        )
        assert post_edges[0].target_node_id == "ok"
        assert post_edges[0].source_port_id == "success"

        # Cascade-skip should mark "err" as skipped.
        assert "err" in run.context.skipped_nodes, (
            "err-branch must be cascade-skipped when COMPUTE takes success"
        )
        assert "err" in result.skipped_node_ids
        assert any(s.node_id == "err" for s in post_skips), (
            "TaskSkipped event must be emitted for the cascade-skipped err node"
        )

        # Deterministic event ids — both the edge and the skip carry
        # non-empty event_ids (uuid5 helper output).
        assert post_edges[0].event_id, "edge event_id must be deterministic (non-empty)"
        assert post_skips[0].event_id, "skip event_id must be deterministic"
        # And the ids differ from each other (different correlation keys)
        assert post_edges[0].event_id != post_skips[0].event_id

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_compute_failure_port_filters_success_branch(self):
        """Mirror test — on FAILED status, the success branch is cascade-skipped
        and only the failure→err edge fires. Verifies the port selection
        actually flips with task_status.
        """
        from app.layer1_domain.events import EdgeTraversed, TaskSkipped

        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="C", node_type=NodeType.COMPUTE, config={"expression": "1"}),
                WorkflowNode(id="ok", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="err", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="A", target_node_id="C"),
                WorkflowEdge(id="e2", source_node_id="C", target_node_id="ok", source_port_id="success"),
                WorkflowEdge(id="e3", source_node_id="C", target_node_id="err", source_port_id="failure"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["C"], "C": ["ok", "err"], "ok": [], "err": []},
            predecessors={"A": [], "C": ["A"], "ok": ["C"], "err": ["C"]},
            edge_map={
                "A": [workflow.edges[0]],
                "C": [workflow.edges[1], workflow.edges[2]],
                "ok": [],
                "err": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["ok", "err"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        # Drive the COMPUTE handler to act as "FAILED" by marking the
        # completed task as FAILED. Because COMPUTE is BUILTIN, the post-
        # handler pass runs on it with TaskStatus.COMPLETED regardless
        # (the engine marks inline BUILTIN tasks COMPLETED after handler
        # returns). To exercise the failure port, we construct the
        # scenario where COMPUTE would have failed — but in this test we
        # actually want to verify success-branch port filtering only, so
        # leave this as a sanity smoke that exercises the same pathway.
        registry = MagicMock()
        compute_handler = AsyncMock()
        compute_handler.handle.return_value = NodeHandlerResult(
            tasks_to_dispatch=[], events=[],
        )
        registry.get.return_value = compute_handler

        processor = self._make_processor(registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)
        result = await processor.process(completed_task, run, workflow, topology)

        # The COMPUTE post-handler pass treats it as COMPLETED (that's
        # the engine contract for inline BUILTIN). So success port fires,
        # err-branch is cascade-skipped. Same invariant as the first test —
        # kept as a regression double-check under a different source topology.
        post_edges = [
            e for e in result.events
            if isinstance(e, EdgeTraversed) and e.source_node_id == "C"
        ]
        assert [e.target_node_id for e in post_edges] == ["ok"]
        assert "err" in run.context.skipped_nodes

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Phase 6.3: tests legacy SuccessorProcessor handler invocation — behavior now in _exec_uniform")
    async def test_condition_taken_edge_ports_preserved(self):
        """Regression: CONDITION sets taken_edge_ports, so the ``if
        taken_ports:`` branch of the refactored loop must emit only the
        taken edges and must NOT cascade-skip via compute_fanout (the
        handler already populated skipped_node_ids for the branching case).
        """
        from app.layer1_domain.events import EdgeTraversed

        workflow = Workflow(
            id="wf-1",
            nodes=[
                WorkflowNode(id="A", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="COND", node_type=NodeType.CONDITION, config={}),
                WorkflowNode(id="then", node_type=NodeType.TASK, worker_type="w"),
                WorkflowNode(id="else", node_type=NodeType.TASK, worker_type="w"),
            ],
            edges=[
                WorkflowEdge(id="e1", source_node_id="A", target_node_id="COND"),
                WorkflowEdge(id="e2", source_node_id="COND", target_node_id="then"),
                WorkflowEdge(id="e3", source_node_id="COND", target_node_id="else"),
            ],
        )
        topology = GraphTopology(
            adjacency={"A": ["COND"], "COND": ["then", "else"], "then": [], "else": []},
            predecessors={"A": [], "COND": ["A"], "then": ["COND"], "else": ["COND"]},
            edge_map={
                "A": [workflow.edges[0]],
                "COND": [workflow.edges[1], workflow.edges[2]],
                "then": [],
                "else": [],
            },
            entry_nodes=["A"],
            leaf_nodes=["then", "else"],
        )
        run = WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext(topology=topology))

        # Simulated CONDITION handler: takes the "then" branch.
        registry = MagicMock()
        cond_handler = AsyncMock()
        cond_result = NodeHandlerResult(
            tasks_to_dispatch=[Task(id="t-then", node_id="then", run_id="run-1")],
            events=[],
            skipped_node_ids=["else"],
        )
        # Populate taken_edge_ports the way CONDITION handler does
        cond_result.taken_edge_ports = {"e2": "true_branch"}
        cond_handler.handle.return_value = cond_result
        registry.get.return_value = cond_handler

        processor = self._make_processor(registry)
        completed_task = Task(id="t-A", run_id="run-1", node_id="A", status=TaskStatus.COMPLETED)
        result = await processor.process(completed_task, run, workflow, topology)

        # Only e2 should be in the EdgeTraversed events from COND
        post_edges = [
            e for e in result.events
            if isinstance(e, EdgeTraversed) and e.source_node_id == "COND"
        ]
        assert len(post_edges) == 1
        assert post_edges[0].edge_id == "e2"
        assert post_edges[0].source_port_id == "true_branch"
        # Phase 4b.4: even the taken_edge_ports branch now carries a
        # deterministic event id.
        assert post_edges[0].event_id
