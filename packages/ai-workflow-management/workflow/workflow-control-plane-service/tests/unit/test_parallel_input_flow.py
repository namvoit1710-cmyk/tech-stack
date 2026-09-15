"""Integration tests for parallel INPUT node execution.

Verifies that the workflow engine correctly handles the topology:

    TRIGGER → PARALLEL → [INPUT_A, INPUT_B] → MERGE → TASK_FINAL

Both INPUT nodes must become PENDING simultaneously, be resumable
independently, and the MERGE must wait until both branches complete.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def _wire_async_repo_mocks(repo):
    """Make a MagicMock-based repo cooperate with the engine's
    ``await repo.a<method>(...)`` calls (Task #19)."""
    if not isinstance(repo, MagicMock):
        return repo
    repo.aget_by_id = AsyncMock(side_effect=lambda *a, **kw: repo.get_by_id(*a, **kw))
    repo.asave = AsyncMock(side_effect=lambda *a, **kw: repo.save(*a, **kw))
    repo.alist_by_run = AsyncMock(
        side_effect=lambda *a, **kw: repo.list_by_run(*a, **kw),
    )
    return repo

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.events import InputRequested
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.parallel_handler import ParallelNodeHandler
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, TraversalResolverStrategy
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from tests.conftest import DummyEventPublisher, DummyStageRecorder
from tests.helpers.node_driver import drive_node


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _build_parallel_input_workflow() -> tuple[Workflow, GraphTopology]:
    """Build: TRIGGER → PARALLEL → [INPUT_A, INPUT_B] → MERGE → TASK_FINAL"""
    nodes = [
        WorkflowNode(id="trigger", name="Trigger", node_type=NodeType.TRIGGER),
        WorkflowNode(id="parallel", name="Parallel", node_type=NodeType.PARALLEL),
        WorkflowNode(
            id="input_a", name="Input A", node_type=NodeType.INPUT,
            config={"input_schema": [{"key": "name", "type": "string"}]},
        ),
        WorkflowNode(
            id="input_b", name="Input B", node_type=NodeType.INPUT,
            config={"input_schema": [{"key": "email", "type": "string"}]},
        ),
        WorkflowNode(id="merge", name="Merge", node_type=NodeType.MERGE),
        WorkflowNode(
            id="task_final", name="Final", node_type=NodeType.TASK,
            worker_type="final-worker",
        ),
    ]
    edges = [
        WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="parallel"),
        WorkflowEdge(id="e2", source_node_id="parallel", target_node_id="input_a"),
        WorkflowEdge(id="e3", source_node_id="parallel", target_node_id="input_b"),
        WorkflowEdge(id="e4", source_node_id="input_a", target_node_id="merge"),
        WorkflowEdge(id="e5", source_node_id="input_b", target_node_id="merge"),
        WorkflowEdge(id="e6", source_node_id="merge", target_node_id="task_final"),
    ]
    workflow = Workflow(id="wf-par", name="Parallel Input Test", nodes=nodes, edges=edges)

    topology = GraphTopology(
        adjacency={
            "trigger": ["parallel"],
            "parallel": ["input_a", "input_b"],
            "input_a": ["merge"],
            "input_b": ["merge"],
            "merge": ["task_final"],
            "task_final": [],
        },
        predecessors={
            "trigger": [],
            "parallel": ["trigger"],
            "input_a": ["parallel"],
            "input_b": ["parallel"],
            "merge": ["input_a", "input_b"],
            "task_final": ["merge"],
        },
        edge_map={
            "trigger": [edges[0]],
            "parallel": [edges[1], edges[2]],
            "input_a": [edges[3]],
            "input_b": [edges[4]],
            "merge": [edges[5]],
            "task_final": [],
        },
        entry_nodes=["trigger"],
        leaf_nodes=["task_final"],
    )
    return workflow, topology


def _build_engine(
    task_repo, event_publisher, stage_recorder,
) -> tuple[OrchestrationEngine, TaskFactory]:
    """Wire a real engine with real handlers and in-memory repos."""
    resolver = VariableResolver(strategies=[TraversalResolverStrategy()])
    task_factory = TaskFactory()

    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.PARALLEL, ParallelNodeHandler(resolver))
    registry.register(NodeType.INPUT, InputNodeHandler(resolver))
    registry.register(NodeType.MERGE, MergeNodeHandler())
    registry.register(NodeType.TASK, TaskNodeHandler(resolver))

    successor_processor = SuccessorProcessor(registry, task_factory)
    run_repo = _wire_async_repo_mocks(MagicMock())

    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=_wire_async_repo_mocks(task_repo),
        run_repo=run_repo,
        event_publisher=event_publisher,
    )
    return engine, task_factory


@pytest.fixture
def setup():
    """Return (engine, task_factory, workflow, topology, run, task_repo, event_publisher)."""
    task_repo = MagicMock()
    task_repo.list_by_run.return_value = []
    _wire_async_repo_mocks(task_repo)
    event_publisher = DummyEventPublisher()
    stage_recorder = DummyStageRecorder()
    engine, task_factory = _build_engine(task_repo, event_publisher, stage_recorder)

    workflow, topology = _build_parallel_input_workflow()
    run = WorkflowRun(
        id="run-1",
        workflow_id="wf-par",
        status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology,
            variables={"trigger": {"body": {}}},
        ),
    )
    return engine, task_factory, workflow, topology, run, task_repo, event_publisher


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParallelInputFanOut:
    """Test A: PARALLEL fans out to two INPUT nodes — both become PENDING."""

    @pytest.mark.asyncio
    async def test_both_inputs_become_pending(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        # Pre-complete trigger and parallel (builtin nodes handled inline)
        run.context.completed_nodes.add("trigger")
        run.context.completed_nodes.add("parallel")

        # Simulate PARALLEL handler output: two tasks for each INPUT branch
        task_a = task_factory.create_task(workflow.get_node("input_a"), run)
        task_b = task_factory.create_task(workflow.get_node("input_b"), run)

        await drive_node(engine, task_a, run, workflow, topology)
        await drive_node(engine, task_b, run, workflow, topology)

        # Both tasks should be saved as PENDING
        saved_tasks = [call.args[0] for call in task_repo.save.call_args_list]
        pending_tasks = [t for t in saved_tasks if t.status == TaskStatus.PENDING]
        assert len(pending_tasks) == 2

        pending_node_ids = {t.node_id for t in pending_tasks}
        assert pending_node_ids == {"input_a", "input_b"}

    @pytest.mark.asyncio
    async def test_input_requested_events_for_both(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.add("trigger")
        run.context.completed_nodes.add("parallel")

        task_a = task_factory.create_task(workflow.get_node("input_a"), run)
        task_b = task_factory.create_task(workflow.get_node("input_b"), run)

        await drive_node(engine, task_a, run, workflow, topology)
        await drive_node(engine, task_b, run, workflow, topology)
        await engine.drain_events()  # drain EventCollector to event_pub

        input_events = [
            ev for _, ev in event_pub.events if isinstance(ev, InputRequested)
        ]
        assert len(input_events) == 2
        event_node_ids = {ev.node_id for ev in input_events}
        assert event_node_ids == {"input_a", "input_b"}

    @pytest.mark.asyncio
    async def test_input_nodes_not_in_completed(self, setup):
        """INPUT nodes should NOT be added to completed_nodes (they are PENDING)."""
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.add("trigger")
        run.context.completed_nodes.add("parallel")

        task_a = task_factory.create_task(workflow.get_node("input_a"), run)
        task_b = task_factory.create_task(workflow.get_node("input_b"), run)

        await drive_node(engine, task_a, run, workflow, topology)
        await drive_node(engine, task_b, run, workflow, topology)

        assert "input_a" not in run.context.completed_nodes
        assert "input_b" not in run.context.completed_nodes


class TestResumeFirstInput:
    """Test B: Resume INPUT_A — MERGE should wait because INPUT_B is still pending."""

    @pytest.mark.skip(reason=(
        "Phase 6.3: the AND-join dispatch gate was retired — compute_fanout is "
        "fire-on-first-arrival, so MERGE IS dispatched when the first branch "
        "completes; its all-branches semantics now live in the merge handler "
        "(merge_quorum_buffers). 'MERGE waits for all branches' is covered by "
        "test_merge_strategies.py and integration test_gated_parallel_merge.py. "
        "Matches the already-skipped sibling test_merge_dispatched_after_both_complete."
    ))
    @pytest.mark.asyncio
    async def test_merge_not_dispatched_after_first_resume(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.update({"trigger", "parallel"})

        # Simulate: INPUT_A was resumed and completed
        run.context.completed_nodes.add("input_a")
        run.context.variables["input_a"] = {"output": {"name": "Alice"}}

        # Create a completed task for input_a to feed into successor processing
        completed_task_a = Task(
            id="ta", run_id="run-1", node_id="input_a",
            status=TaskStatus.COMPLETED,
        )

        # Process successors for input_a
        result = await engine.process_successors(
            completed_task_a, run, workflow, topology,
        )

        # MERGE should NOT be dispatched (input_b not done)
        dispatched_node_ids = {t.node_id for t in result.tasks_to_dispatch}
        assert "merge" not in dispatched_node_ids
        assert "task_final" not in dispatched_node_ids

    @pytest.mark.asyncio
    async def test_run_pending_with_one_pending(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.update({"trigger", "parallel", "input_a"})

        # One task completed, one still pending — all active tasks are PENDING
        task_repo.list_by_run.return_value = [
            Task(id="ta", run_id="run-1", node_id="input_a", status=TaskStatus.COMPLETED),
            Task(id="tb", run_id="run-1", node_id="input_b", status=TaskStatus.PENDING),
        ]

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.PENDING


class TestResumeSecondInput:
    """Test C: Resume INPUT_B — MERGE fires, TASK_FINAL is dispatched."""

    @pytest.mark.skip(reason="Phase 6.3: tests legacy recursive BUILTIN processing in SuccessorProcessor — behavior now in _exec_uniform")
    @pytest.mark.asyncio
    async def test_merge_dispatched_after_both_complete(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        # Both branches completed
        run.context.completed_nodes.update({
            "trigger", "parallel", "input_a", "input_b",
        })
        run.context.variables["input_a"] = {"output": {"name": "Alice"}}
        run.context.variables["input_b"] = {"output": {"email": "bob@test.com"}}

        completed_task_b = Task(
            id="tb", run_id="run-1", node_id="input_b",
            status=TaskStatus.COMPLETED,
        )

        result = await engine.process_successors(
            completed_task_b, run, workflow, topology,
        )

        dispatched_node_ids = {t.node_id for t in result.tasks_to_dispatch}
        # MERGE is builtin, so successor processor handles it inline and
        # dispatches its successors. task_final should be in the output.
        assert "task_final" in dispatched_node_ids

    @pytest.mark.skip(reason="Phase 6.3: tests legacy recursive BUILTIN processing in SuccessorProcessor — behavior now in _exec_uniform")
    @pytest.mark.asyncio
    async def test_merge_added_to_completed(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.update({
            "trigger", "parallel", "input_a", "input_b",
        })
        run.context.variables["input_a"] = {"output": {}}
        run.context.variables["input_b"] = {"output": {}}

        completed_task_b = Task(
            id="tb", run_id="run-1", node_id="input_b",
            status=TaskStatus.COMPLETED,
        )

        await engine.process_successors(
            completed_task_b, run, workflow, topology,
        )

        assert "merge" in run.context.completed_nodes


class TestRunCompletionWithPendingTasks:
    """Test D: Run transitions to PENDING when all active tasks are PENDING."""

    @pytest.mark.asyncio
    async def test_run_pending_with_both_pending(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.update({"trigger", "parallel"})

        task_repo.list_by_run.return_value = [
            Task(id="ta", run_id="run-1", node_id="input_a", status=TaskStatus.PENDING),
            Task(id="tb", run_id="run-1", node_id="input_b", status=TaskStatus.PENDING),
        ]

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.PENDING

    @pytest.mark.asyncio
    async def test_run_completes_after_all_finish(self, setup):
        engine, task_factory, workflow, topology, run, task_repo, event_pub = setup

        run.context.completed_nodes.update({
            "trigger", "parallel", "input_a", "input_b", "merge", "task_final",
        })

        task_repo.list_by_run.return_value = [
            Task(id="ta", run_id="run-1", node_id="input_a", status=TaskStatus.COMPLETED),
            Task(id="tb", run_id="run-1", node_id="input_b", status=TaskStatus.COMPLETED),
            Task(id="tm", run_id="run-1", node_id="merge", status=TaskStatus.COMPLETED),
            Task(id="tf", run_id="run-1", node_id="task_final", status=TaskStatus.COMPLETED),
        ]

        await engine.check_run_completion(run, workflow)

        assert run.status == RunStatus.COMPLETED
