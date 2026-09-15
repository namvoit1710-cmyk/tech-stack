"""Tests for workflow-calls-workflow invocation loop."""

import pytest

pytestmark = pytest.mark.skip(reason="legacy inline-execution coverage — rewrite to use tests.helpers.queue_runner for queued-mode regression coverage")

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.events import ChildRunStarted, ChildRunCompleted
from app.layer1_domain.exceptions import CrossWorkflowCycleError, WorkflowExecutionError
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.node_handlers.base import NodeHandlerResult, NodeHandlerRegistry
from app.layer2_application.node_handlers.workflow_handler import WorkflowNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.node_handlers.task_handler import TaskNodeHandler
from app.layer2_application.node_handlers.input_handler import InputNodeHandler
from app.layer2_application.node_handlers.file_upload_handler import FileUploadNodeHandler
from app.layer2_application.node_handlers.human_action_handler import HumanActionNodeHandler
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer2_application.interfaces.orchestration_queue_interface import ResumeTaskAction
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskUseCase,
    ResumePendingTaskCommand,
)
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
)

from tests.conftest import (
    DummyLogger,
    DummyEventPublisher,
    DummyStageRecorder,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import MemoryStageHistoryRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_resolver():
    return VariableResolver(
        strategies=[MetadataResolverStrategy(), TraversalResolverStrategy()]
    )


def _make_handler_registry(resolver):
    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.TASK, TaskNodeHandler(resolver))
    registry.register(NodeType.WORKFLOW, WorkflowNodeHandler(resolver))
    registry.register(NodeType.INPUT, InputNodeHandler(resolver))
    registry.register(NodeType.FILE_UPLOAD, FileUploadNodeHandler(resolver))
    registry.register(NodeType.HUMAN_ACTION, HumanActionNodeHandler(resolver))
    return registry


def _make_engine(workflow_repo, run_repo, task_repo, event_publisher=None):
    resolver = _make_resolver()
    registry = _make_handler_registry(resolver)
    stage_recorder = StageRecorder(MemoryStageHistoryRepository())
    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(registry, task_factory)
    return OrchestrationEngine(
        handler_registry=registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=event_publisher or DummyEventPublisher(),
        workflow_repo=workflow_repo,
        variable_resolver=resolver,
    )


def _parent_workflow():
    """Parent workflow: TRIGGER → WORKFLOW → TASK (leaf)."""
    return Workflow(
        id="parent-wf",
        name="Parent Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="wf-node",
                name="CallChild",
                node_type=NodeType.WORKFLOW,
                config={
                    "workflow_id": "child-wf",
                    "input_mapping": {},
                    "on_failure": "fail",
                },
            ),
            WorkflowNode(id="final-task", name="FinalTask", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="wf-node"),
            WorkflowEdge(source_node_id="wf-node", target_node_id="final-task"),
        ],
    )


def _child_workflow():
    """Simple child workflow: TRIGGER (leaf)."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(id="child-trigger", name="ChildStart", node_type=NodeType.TRIGGER),
        ],
        edges=[],
    )


def _child_workflow_with_task():
    """Child workflow with a TASK node: TRIGGER → TASK (leaf)."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(id="child-trigger", name="ChildStart", node_type=NodeType.TRIGGER),
            WorkflowNode(id="child-task", name="ChildTask", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[
            WorkflowEdge(source_node_id="child-trigger", target_node_id="child-task"),
        ],
    )


def _child_workflow_with_task_entry():
    """Child workflow whose entry node is a TASK."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(id="child-task", name="ChildTask", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[],
    )


def _child_workflow_with_input():
    """Child workflow with an INPUT node: TRIGGER → INPUT (leaf)."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(id="child-trigger", name="ChildStart", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="child-input",
                name="ChildInput",
                node_type=NodeType.INPUT,
                config={"input_schema": [{"key": "comment", "type": "string"}]},
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="child-trigger", target_node_id="child-input"),
        ],
    )


def _parent_workflow_with_input_then_child():
    """Parent workflow: TRIGGER → INPUT → WORKFLOW → TASK."""
    return Workflow(
        id="parent-wf",
        name="Parent Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="parent-input",
                name="ParentInput",
                node_type=NodeType.INPUT,
                config={"input_schema": [{"key": "approval", "type": "string"}]},
            ),
            WorkflowNode(
                id="wf-node",
                name="CallChild",
                node_type=NodeType.WORKFLOW,
                config={
                    "workflow_id": "child-wf",
                    "input_mapping": {},
                    "on_failure": "fail",
                },
            ),
            WorkflowNode(id="final-task", name="FinalTask", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="parent-input"),
            WorkflowEdge(source_node_id="parent-input", target_node_id="wf-node"),
            WorkflowEdge(source_node_id="wf-node", target_node_id="final-task"),
        ],
    )


def _child_workflow_with_file_upload():
    """Child workflow with a FILE_UPLOAD node: TRIGGER → FILE_UPLOAD (leaf)."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(id="child-trigger", name="ChildStart", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="child-upload",
                name="ChildUpload",
                node_type=NodeType.FILE_UPLOAD,
                config={"artifact_key": "child_artifact"},
            ),
        ],
        edges=[
            WorkflowEdge(source_node_id="child-trigger", target_node_id="child-upload"),
        ],
    )


def _child_workflow_with_human_action():
    """Child workflow with a HUMAN_ACTION entry (leaf): just the HUMAN_ACTION."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(
                id="child-ha",
                name="ChildHumanAction",
                node_type=NodeType.HUMAN_ACTION,
                config={
                    "instruction": "Approve the child step",
                    "input_schema": [],
                    "confirm_label": "Approve",
                    "reject_label": "Reject",
                },
            ),
        ],
        edges=[],
    )


def _child_workflow_with_human_action_then_task():
    """Child workflow: HUMAN_ACTION (entry) → TASK (leaf)."""
    return Workflow(
        id="child-wf",
        name="Child Workflow",
        nodes=[
            WorkflowNode(
                id="child-ha",
                name="ChildHumanAction",
                node_type=NodeType.HUMAN_ACTION,
                config={
                    "instruction": "Approve the child step",
                    "input_schema": [],
                    "confirm_label": "Approve",
                    "reject_label": "Reject",
                },
            ),
            WorkflowNode(
                id="child-task",
                name="ChildTask",
                node_type=NodeType.TASK,
                worker_type="w2",
            ),
        ],
        edges=[
            WorkflowEdge(
                source_node_id="child-ha",
                target_node_id="child-task",
                source_port_id="confirm",
            ),
        ],
    )


def _parent_workflow_with_file_upload_then_child():
    """Parent workflow: TRIGGER → FILE_UPLOAD → WORKFLOW → TASK."""
    return Workflow(
        id="parent-wf",
        name="Parent Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="parent-upload",
                name="ParentUpload",
                node_type=NodeType.FILE_UPLOAD,
                config={"artifact_key": "parent_artifact"},
            ),
            WorkflowNode(
                id="wf-node",
                name="CallChild",
                node_type=NodeType.WORKFLOW,
                config={
                    "workflow_id": "child-wf",
                    "input_mapping": {},
                    "on_failure": "fail",
                },
            ),
            WorkflowNode(id="final-task", name="FinalTask", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="parent-upload"),
            WorkflowEdge(source_node_id="parent-upload", target_node_id="wf-node"),
            WorkflowEdge(source_node_id="wf-node", target_node_id="final-task"),
        ],
    )


# ---------------------------------------------------------------------------
# Step 1 & 2: NodeHandlerResult.requires_child_run
# ---------------------------------------------------------------------------

class TestRequiresChildRunFlag:

    def test_default_is_false(self):
        result = NodeHandlerResult()
        assert result.requires_child_run is False

    def test_merge_propagates_flag(self):
        a = NodeHandlerResult()
        b = NodeHandlerResult()
        b.requires_child_run = True
        a.merge(b)
        assert a.requires_child_run is True

    def test_merge_does_not_clear_flag(self):
        a = NodeHandlerResult()
        a.requires_child_run = True
        b = NodeHandlerResult()
        b.requires_child_run = False
        a.merge(b)
        assert a.requires_child_run is True

    @pytest.mark.asyncio
    async def test_workflow_handler_sets_flag(self):
        resolver = _make_resolver()
        handler = WorkflowNodeHandler(resolver)
        node = WorkflowNode(
            id="wf-node", name="CallChild", node_type=NodeType.WORKFLOW,
            config={"workflow_id": "child-wf"},
        )
        run = WorkflowRun(id="run-1", workflow_id="parent-wf", status=RunStatus.RUNNING,
                          input_data={"key": "value"})
        wf = Workflow(id="parent-wf", nodes=[node], edges=[])
        topology = GraphTopology.from_workflow(wf)
        tf = TaskFactory()

        result = await handler.handle(node, run, wf, topology, None, tf)
        assert result.requires_child_run is True
        assert len(result.tasks_to_dispatch) == 1

        # Verify predefined_run_id and parent_input in metadata
        metadata = result.state_updates["wf-node"]["metadata"]
        assert "predefined_run_id" in metadata
        # predefined_run_id is deterministic: {run_id}_{node_id}_{generation}.
        # No wall-clock component, so a redelivered/replayed dispatch of the
        # same WORKFLOW node yields the SAME child run id (idempotent) instead
        # of spawning an orphaned duplicate child run.
        predefined_id = metadata["predefined_run_id"]
        assert predefined_id == "run-1_wf-node_0"
        # Re-invoking the handler for the same (run, node, generation) must
        # produce an identical id — this is the determinism guarantee.
        result2 = await handler.handle(node, run, wf, topology, None, tf)
        assert result2.state_updates["wf-node"]["metadata"]["predefined_run_id"] == predefined_id
        assert metadata["parent_input"] == {"key": "value"}


# ---------------------------------------------------------------------------
# Step 6: _start_child_workflow (happy path)
# ---------------------------------------------------------------------------

class TestStartChildWorkflow:

    @pytest.mark.asyncio
    async def test_child_run_created_and_completed_inline(self):
        """When child workflow has only TRIGGER (auto-completes), the whole
        chain should complete: child run → parent WORKFLOW node → parent."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)
        await engine.drain_events()  # drain EventCollector to event_pub

        # Re-fetch parent_run from repo (engine modifies the repo copy)
        parent_run = run_repo.get_by_id("parent-run")

        # Verify child run was created
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id is not None

        child_run = run_repo.get_by_id(child_run_id)
        assert child_run is not None
        assert child_run.parent_run_id == "parent-run"
        assert child_run.parent_node_id == "wf-node"
        assert child_run.depth_level == 1
        assert child_run.status == RunStatus.COMPLETED

        # Parent WORKFLOW node should be completed
        assert "wf-node" in parent_run.context.completed_nodes

        # Child events were published
        child_started = [
            e for t, e in event_pub.events if t == "child_run.started"
        ]
        assert len(child_started) == 1
        assert child_started[0].child_workflow_id == "child-wf"

    @pytest.mark.asyncio
    async def test_child_run_with_task_stays_running(self):
        """When child workflow has a TASK node, child stays RUNNING (task
        not dispatched to a real worker), and parent stays RUNNING too."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_task()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        child_run_id = parent_run.context.child_runs.get("wf-node")
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.RUNNING

        # Parent should still be running
        parent_run = run_repo.get_by_id("parent-run")
        assert parent_run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_child_run_with_task_entry_stays_running(self):
        """A child workflow whose entry node is TASK should dispatch and keep both runs active."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_task_entry()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        child_run_id = parent_run.context.child_runs.get("wf-node")
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.RUNNING

        child_tasks = task_repo.list_by_run(child_run_id)
        child_task = next(t for t in child_tasks if t.node_id == "child-task")
        assert child_task.status == TaskStatus.RUNNING

        parent_run = run_repo.get_by_id("parent-run")
        assert parent_run.status == RunStatus.RUNNING
        assert "wf-node" not in parent_run.context.completed_nodes

    @pytest.mark.asyncio
    async def test_child_run_with_input_stays_running_until_resumed(self):
        """A child INPUT node should pause the child run and keep the parent waiting."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_input()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs.get("wf-node")
        child_run = run_repo.get_by_id(child_run_id)

        assert child_run.status == RunStatus.PENDING
        assert "wf-node" not in parent_run.context.completed_nodes

        child_tasks = task_repo.list_by_run(child_run_id)
        pending_tasks = [t for t in child_tasks if t.node_id == "child-input"]
        assert len(pending_tasks) == 1
        assert pending_tasks[0].status == TaskStatus.PENDING

        parent_run = run_repo.get_by_id("parent-run")
        assert parent_run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_parent_and_child_input_nodes_resume_in_sequence(self):
        """A parent INPUT can resume into a child INPUT, and the parent should only advance after the child resumes."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow_with_input_then_child()
        child_wf = _child_workflow_with_input()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)
        resume_use_case = ResumePendingTaskUseCase(
            logger=DummyLogger(),
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=engine._stage_recorder,
            event_publisher=event_pub,
            orchestration_engine=engine,
        )

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)

        parent_tasks = task_repo.list_by_run("parent-run")
        parent_input_task = next(t for t in parent_tasks if t.node_id == "parent-input")
        assert parent_input_task.status == TaskStatus.PENDING
        assert not run_repo.get_by_id("parent-run").context.child_runs

        parent_resume = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id="parent-run",
                task_id=parent_input_task.id,
                updated_input={"approval": "approved"},
            )
        )
        assert parent_resume.resumed is True

        parent_run = run_repo.get_by_id("parent-run")
        assert "parent-input" in parent_run.context.completed_nodes
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id is not None

        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.PENDING
        child_tasks = task_repo.list_by_run(child_run_id)
        child_input_task = next(t for t in child_tasks if t.node_id == "child-input")
        assert child_input_task.status == TaskStatus.PENDING
        assert "wf-node" not in parent_run.context.completed_nodes

        child_resume = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id=child_run_id,
                task_id=child_input_task.id,
                updated_input={"comment": "child approved"},
            )
        )
        assert child_resume.resumed is True

        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED

        parent_run = run_repo.get_by_id("parent-run")
        assert "wf-node" in parent_run.context.completed_nodes
        final_tasks = [t for t in task_repo.list_by_run("parent-run") if t.node_id == "final-task"]
        assert len(final_tasks) == 1
        assert final_tasks[0].status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_child_run_with_file_upload_stays_pending_until_resumed(self):
        """A child FILE_UPLOAD node should pause the child run and keep the parent waiting."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_file_upload()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs.get("wf-node")
        child_run = run_repo.get_by_id(child_run_id)

        assert child_run.status == RunStatus.PENDING
        assert "wf-node" not in parent_run.context.completed_nodes

        child_tasks = task_repo.list_by_run(child_run_id)
        pending_tasks = [t for t in child_tasks if t.node_id == "child-upload"]
        assert len(pending_tasks) == 1
        assert pending_tasks[0].status == TaskStatus.PENDING

        parent_run = run_repo.get_by_id("parent-run")
        assert parent_run.status == RunStatus.RUNNING

    @pytest.mark.asyncio
    async def test_parent_and_child_file_upload_nodes_resume_in_sequence(self):
        """A parent FILE_UPLOAD can resume into a child FILE_UPLOAD, and the parent should only advance after the child resumes."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow_with_file_upload_then_child()
        child_wf = _child_workflow_with_file_upload()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)
        resume_use_case = ResumePendingTaskUseCase(
            logger=DummyLogger(),
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=engine._stage_recorder,
            event_publisher=event_pub,
            orchestration_engine=engine,
        )

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)

        parent_tasks = task_repo.list_by_run("parent-run")
        parent_upload_task = next(t for t in parent_tasks if t.node_id == "parent-upload")
        assert parent_upload_task.status == TaskStatus.PENDING
        assert not run_repo.get_by_id("parent-run").context.child_runs

        parent_resume = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id="parent-run",
                task_id=parent_upload_task.id,
                updated_input={
                    "file": {
                        "file_id": "parent-file-id",
                        "filename": "parent.csv",
                        "content_type": "text/csv",
                        "size_bytes": 12,
                    }
                },
            )
        )
        assert parent_resume.resumed is True

        parent_run = run_repo.get_by_id("parent-run")
        assert "parent-upload" in parent_run.context.completed_nodes
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id is not None

        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.PENDING
        child_tasks = task_repo.list_by_run(child_run_id)
        child_upload_task = next(t for t in child_tasks if t.node_id == "child-upload")
        assert child_upload_task.status == TaskStatus.PENDING
        assert "wf-node" not in parent_run.context.completed_nodes

        child_resume = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id=child_run_id,
                task_id=child_upload_task.id,
                updated_input={
                    "file": {
                        "file_id": "child-file-id",
                        "filename": "child.csv",
                        "content_type": "text/csv",
                        "size_bytes": 24,
                    }
                },
            )
        )
        assert child_resume.resumed is True

        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED

        parent_run = run_repo.get_by_id("parent-run")
        assert "wf-node" in parent_run.context.completed_nodes
        final_tasks = [t for t in task_repo.list_by_run("parent-run") if t.node_id == "final-task"]
        assert len(final_tasks) == 1
        assert final_tasks[0].status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_child_run_with_human_action_entry_stays_pending_until_resumed(self):
        """A HUMAN_ACTION entry in a child workflow should pause the child run.

        Reproduces the user-reported scenario: child workflow has
        HUMAN_ACTION as its entry node. Until the user resumes the action,
        the child should be PENDING and the parent's WORKFLOW node not
        completed.
        """
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_human_action()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id is not None

        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.PENDING
        assert "wf-node" not in parent_run.context.completed_nodes

        child_tasks = task_repo.list_by_run(child_run_id)
        ha_tasks = [t for t in child_tasks if t.node_id == "child-ha"]
        assert len(ha_tasks) == 1
        assert ha_tasks[0].status == TaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_child_run_with_human_action_entry_resumes_and_completes_parent(self):
        """When the user resumes a HUMAN_ACTION that is the child's entry
        node (and the child's only node), the child should COMPLETE and
        the parent's WORKFLOW node should advance to its successor.

        Bug: production reports that the child is BLOCKED after resume and
        the parent never advances.
        """
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_human_action()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)
        resume_use_case = ResumePendingTaskUseCase(
            logger=DummyLogger(),
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=engine._stage_recorder,
            event_publisher=event_pub,
            orchestration_engine=engine,
        )

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs["wf-node"]
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.PENDING

        child_tasks = task_repo.list_by_run(child_run_id)
        child_ha_task = next(t for t in child_tasks if t.node_id == "child-ha")
        assert child_ha_task.status == TaskStatus.PENDING

        # Resume the child's HUMAN_ACTION with confirm
        result = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id=child_run_id,
                task_id=child_ha_task.id,
                updated_input={"_action": "confirm"},
            )
        )
        assert result.resumed is True

        # Child should now be COMPLETED (HUMAN_ACTION is also the leaf)
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child run not completed after HUMAN_ACTION resume; "
            f"status={child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        # Parent should have advanced past the WORKFLOW node
        parent_run = run_repo.get_by_id("parent-run")
        assert "wf-node" in parent_run.context.completed_nodes, (
            f"Parent did not advance past WORKFLOW node after child completed; "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )
        final_tasks = [t for t in task_repo.list_by_run("parent-run") if t.node_id == "final-task"]
        assert len(final_tasks) == 1
        assert final_tasks[0].status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_child_run_with_human_action_entry_resumes_via_queue_path(self):
        """Production scenario: ResumePendingTask uses the broker queue
        (BrokerOrchestrationQueue) which defers successor processing to a
        worker via orchestrate_resume → orchestrate_continue.

        This test mirrors that path with MemoryOrchestrationQueue + manual
        dequeue. Demonstrates whether the worker correctly advances a child
        run whose entry HUMAN_ACTION was just resumed.
        """
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_human_action()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)
        queue = MemoryOrchestrationQueue()
        resume_use_case = ResumePendingTaskUseCase(
            logger=DummyLogger(),
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=engine._stage_recorder,
            event_publisher=event_pub,
            orchestration_engine=engine,
            orchestration_queue=queue,
        )

        # Drive parent → child setup the same way as the sync test.
        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs["wf-node"]
        child_tasks = task_repo.list_by_run(child_run_id)
        child_ha_task = next(t for t in child_tasks if t.node_id == "child-ha")
        assert child_ha_task.status == TaskStatus.PENDING

        # === Resume via queue path ===
        # resume_pending_task ENQUEUES ResumeTaskAction (does not process inline).
        result = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id=child_run_id,
                task_id=child_ha_task.id,
                updated_input={"_action": "confirm"},
            )
        )
        assert result.resumed is True

        # The use case enqueued a ResumeTaskAction. Dequeue + dispatch to engine
        # to simulate what the OrchestrationWorker does in production.
        action = await queue.dequeue()
        assert isinstance(action, ResumeTaskAction)
        assert action.run_id == child_run_id
        assert action.task_id == child_ha_task.id

        await engine.orchestrate_resume(action.run_id, action.task_id, test_mode=action.test_mode)

        # === Verify final state ===
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED, (
            f"Child run should be COMPLETED after worker processes ResumeTaskAction; "
            f"status={child_run.status}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )

        parent_run = run_repo.get_by_id("parent-run")
        assert "wf-node" in parent_run.context.completed_nodes, (
            f"Parent should advance after child completes via queue path; "
            f"completed_nodes={parent_run.context.completed_nodes}"
        )
        final_tasks = [t for t in task_repo.list_by_run("parent-run") if t.node_id == "final-task"]
        assert len(final_tasks) == 1
        assert final_tasks[0].status == TaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_child_run_with_human_action_entry_then_task_resumes_through(self):
        """When the child has HUMAN_ACTION (entry) → TASK (leaf), resuming
        the HUMAN_ACTION with confirm should dispatch the TASK successor."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_human_action_then_task()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)
        resume_use_case = ResumePendingTaskUseCase(
            logger=DummyLogger(),
            run_repo=run_repo,
            task_repo=task_repo,
            workflow_repo=workflow_repo,
            stage_recorder=engine._stage_recorder,
            event_publisher=event_pub,
            orchestration_engine=engine,
        )

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs["wf-node"]

        child_tasks = task_repo.list_by_run(child_run_id)
        child_ha_task = next(t for t in child_tasks if t.node_id == "child-ha")
        assert child_ha_task.status == TaskStatus.PENDING

        # Resume child's HUMAN_ACTION with confirm → should dispatch child-task
        result = await resume_use_case.execute(
            ResumePendingTaskCommand(
                run_id=child_run_id,
                task_id=child_ha_task.id,
                updated_input={"_action": "confirm"},
            )
        )
        assert result.resumed is True

        child_run = run_repo.get_by_id(child_run_id)
        # HUMAN_ACTION was completed; child-task should now exist in RUNNING
        assert "child-ha" in child_run.context.completed_nodes
        child_tasks = task_repo.list_by_run(child_run_id)
        child_task = [t for t in child_tasks if t.node_id == "child-task"]
        assert len(child_task) == 1, (
            f"child-task was not dispatched after HUMAN_ACTION confirm; "
            f"tasks={[(t.node_id, t.status) for t in child_tasks]}, "
            f"completed_nodes={child_run.context.completed_nodes}, "
            f"skipped_nodes={child_run.context.skipped_nodes}"
        )
        assert child_task[0].status == TaskStatus.RUNNING


# ---------------------------------------------------------------------------
# Step 7 & 8: _complete_parent_tracking_task
# ---------------------------------------------------------------------------

class TestCompleteParentTrackingTask:

    @pytest.mark.asyncio
    async def test_child_completion_resumes_parent(self):
        """Manually completing the child's task should propagate up to parent."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_task()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)

        # Start the parent run
        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)
        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        # The child should be running with a TASK
        child_run_id = parent_run.context.child_runs["wf-node"]
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.RUNNING

        # Find the child's dispatched task and complete it
        child_tasks = task_repo.list_by_run(child_run_id)
        child_task = [t for t in child_tasks if t.node_id == "child-task"][0]
        assert child_task.status == TaskStatus.RUNNING

        child_task.status = TaskStatus.COMPLETED
        child_task.completed_at = "2026-01-01T00:00:00Z"
        task_repo.save(child_task)

        # Process successor in child (no successors for child-task, it's a leaf)
        child_workflow = workflow_repo.get_by_id("child-wf")
        child_run.context.completed_nodes.add("child-task")
        run_repo.save(child_run)
        await engine.check_run_completion(child_run, child_workflow)
        await engine.drain_events()  # drain EventCollector to event_pub

        # After child completes, parent should resume
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.COMPLETED

        # Parent WORKFLOW node should now be completed
        parent_run = run_repo.get_by_id("parent-run")
        assert "wf-node" in parent_run.context.completed_nodes

        # A ChildRunCompleted event should have been published
        completed_events = [
            e for t, e in event_pub.events if t == "child_run.completed"
        ]
        assert len(completed_events) == 1


# ---------------------------------------------------------------------------
# Step 9: _fail_parent_tracking_task
# ---------------------------------------------------------------------------

class TestFailParentTrackingTask:

    @pytest.mark.asyncio
    async def test_child_failure_fails_parent(self):
        """When child run fails and on_failure='fail', parent should fail too."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow_with_task()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)
        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        child_run_id = parent_run.context.child_runs["wf-node"]
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.RUNNING

        # Fail the child task — keep child run as RUNNING so check_run_completion
        # can detect the failure and transition to FAILED
        child_tasks = task_repo.list_by_run(child_run_id)
        child_task = [t for t in child_tasks if t.node_id == "child-task"][0]
        child_task.status = TaskStatus.FAILED
        child_task.error = "boom"
        task_repo.save(child_task)

        child_workflow = workflow_repo.get_by_id("child-wf")
        await engine.check_run_completion(child_run, child_workflow)

        # Child should now be FAILED
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.FAILED

        # Parent should have failed
        parent_run = run_repo.get_by_id("parent-run")
        assert parent_run.status == RunStatus.FAILED

    @pytest.mark.asyncio
    async def test_child_failure_skip_continues_parent(self):
        """When child fails with on_failure='skip', parent should continue."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()
        # Change on_failure to 'skip'
        wf_node = parent_wf.get_node("wf-node")
        wf_node.config["on_failure"] = "skip"
        workflow_repo.save(parent_wf)

        child_wf = _child_workflow_with_task()
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run",
            workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)
        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        child_run_id = parent_run.context.child_runs["wf-node"]
        child_run = run_repo.get_by_id(child_run_id)
        assert child_run.status == RunStatus.RUNNING

        # Fail the child task — keep child run as RUNNING
        child_tasks = task_repo.list_by_run(child_run_id)
        child_task = [t for t in child_tasks if t.node_id == "child-task"][0]
        child_task.status = TaskStatus.FAILED
        child_task.error = "boom"
        task_repo.save(child_task)

        child_workflow = workflow_repo.get_by_id("child-wf")
        await engine.check_run_completion(child_run, child_workflow)

        # Parent should NOT have failed — wf-node skipped, successors processed
        parent_run = run_repo.get_by_id("parent-run")
        assert parent_run.status != RunStatus.FAILED
        assert "wf-node" in parent_run.context.skipped_nodes


# ---------------------------------------------------------------------------
# Cross-workflow cycle detection
# ---------------------------------------------------------------------------

class TestCrossWorkflowCycleDetection:

    @pytest.mark.asyncio
    async def test_self_referencing_cycle_detected(self):
        """A workflow that references itself should raise an error.
        The DAG validator catches self-references before our ancestry check."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        # A workflow that calls itself
        wf = Workflow(
            id="self-wf",
            name="Self Referencing",
            nodes=[
                WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="wf-node",
                    name="CallSelf",
                    node_type=NodeType.WORKFLOW,
                    config={"workflow_id": "self-wf"},
                ),
            ],
            edges=[
                WorkflowEdge(source_node_id="trigger", target_node_id="wf-node"),
            ],
        )
        workflow_repo.save(wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(wf)
        run = WorkflowRun(
            id="run-1",
            workflow_id="self-wf",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(run)

        tf = TaskFactory()
        with pytest.raises((CrossWorkflowCycleError, WorkflowExecutionError)):
            await engine.handle_entry_nodes(topology.entry_nodes, run, wf, topology, tf)

    @pytest.mark.asyncio
    async def test_indirect_cycle_detected(self):
        """A→B→A indirect cycle should raise CrossWorkflowCycleError."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        # Workflow A calls B
        wf_a = Workflow(
            id="wf-a",
            name="Workflow A",
            nodes=[
                WorkflowNode(id="a-trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="a-call-b",
                    name="CallB",
                    node_type=NodeType.WORKFLOW,
                    config={"workflow_id": "wf-b"},
                ),
            ],
            edges=[WorkflowEdge(source_node_id="a-trigger", target_node_id="a-call-b")],
        )
        # Workflow B calls A (creates cycle)
        wf_b = Workflow(
            id="wf-b",
            name="Workflow B",
            nodes=[
                WorkflowNode(id="b-trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(
                    id="b-call-a",
                    name="CallA",
                    node_type=NodeType.WORKFLOW,
                    config={"workflow_id": "wf-a"},
                ),
            ],
            edges=[WorkflowEdge(source_node_id="b-trigger", target_node_id="b-call-a")],
        )
        workflow_repo.save(wf_a)
        workflow_repo.save(wf_b)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(wf_a)
        run = WorkflowRun(
            id="run-a",
            workflow_id="wf-a",
            status=RunStatus.PENDING,
            input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(run)

        tf = TaskFactory()
        with pytest.raises(CrossWorkflowCycleError):
            await engine.handle_entry_nodes(topology.entry_nodes, run, wf_a, topology, tf)
            run.status = RunStatus.RUNNING
            run_repo.save(run)
            await engine.check_run_completion(run, wf_a)


# ---------------------------------------------------------------------------
# Cancel cascading
# ---------------------------------------------------------------------------

class TestCancelCascade:

    @pytest.mark.asyncio
    async def test_cancel_parent_cancels_child(self):
        from app.layer2_application.features.cancel_run.use_cases.cancel_run_use_case import (
            CancelRunUseCase, CancelRunCommand,
        )

        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        workflow_repo = MemoryWorkflowRepository()
        stage_recorder = DummyStageRecorder()
        engine = _make_engine(workflow_repo, run_repo, task_repo)

        child_run = WorkflowRun(
            id="child-run", workflow_id="child-wf",
            status=RunStatus.RUNNING, parent_run_id="parent-run",
        )
        child_task = Task(id="ct-1", run_id="child-run", node_id="cn-1", status=TaskStatus.RUNNING)
        run_repo.save(child_run)
        task_repo.save(child_task)

        parent_run = WorkflowRun(
            id="parent-run", workflow_id="parent-wf",
            status=RunStatus.RUNNING,
            context=RunContext(child_runs={"wf-node": "child-run"}),
        )
        run_repo.save(parent_run)

        uc = CancelRunUseCase(
            logger=DummyLogger(), run_repo=run_repo, task_repo=task_repo,
            stage_recorder=stage_recorder, orchestration_engine=engine,
        )
        result = await uc.execute(CancelRunCommand(run_id="parent-run"))
        assert result.cancelled is True

        child_run = run_repo.get_by_id("child-run")
        assert child_run.status == RunStatus.CANCELLED

        child_task = task_repo.get_by_id("ct-1")
        assert child_task.status == TaskStatus.CANCELLED
        assert child_task.output_metadata.get("reason") == "parent_cancelled"


# ---------------------------------------------------------------------------
# StartRunCommand parent fields
# ---------------------------------------------------------------------------

class TestStartRunCommandParentFields:

    def test_defaults(self):
        from app.layer2_application.features.start_run.use_cases.start_run_use_case import StartRunCommand

        cmd = StartRunCommand(workflow_id="wf-1")
        assert cmd.parent_run_id is None
        assert cmd.parent_node_id is None
        assert cmd.depth_level == 0

    def test_custom_values(self):
        from app.layer2_application.features.start_run.use_cases.start_run_use_case import StartRunCommand

        cmd = StartRunCommand(
            workflow_id="wf-1",
            parent_run_id="pr-1",
            parent_node_id="pn-1",
            depth_level=2,
        )
        assert cmd.parent_run_id == "pr-1"
        assert cmd.parent_node_id == "pn-1"
        assert cmd.depth_level == 2


# ---------------------------------------------------------------------------
# Predefined run ID for child workflows
# ---------------------------------------------------------------------------

class TestPredefinedRunId:

    @pytest.mark.asyncio
    async def test_child_run_uses_predefined_run_id(self):
        """Child run should use the predefined_run_id from the handler metadata."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run", workflow_id="parent-wf",
            status=RunStatus.PENDING, input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)
        await engine.drain_events()  # drain EventCollector to event_pub

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs.get("wf-node")
        assert child_run_id is not None

        # The child_run_id should follow format: {parent_run_id}_{node_id}_{timestamp_millis}
        assert child_run_id.startswith("parent-run_wf-node_")
        timestamp_suffix = child_run_id[len("parent-run_wf-node_"):]
        assert timestamp_suffix.isdigit(), f"Expected timestamp suffix, got: {timestamp_suffix}"

        # Verify ChildRunStarted event contains the predefined_run_id
        child_started = [
            e for t, e in event_pub.events if t == "child_run.started"
        ]
        assert len(child_started) == 1
        assert child_started[0].child_run_id == child_run_id


# ---------------------------------------------------------------------------
# Parent input forwarding
# ---------------------------------------------------------------------------

class TestParentInputForwarding:

    @pytest.mark.asyncio
    async def test_child_receives_parent_input_merged_with_binding(self):
        """Child run input_data should contain parent input merged with input_binding."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = Workflow(
            id="parent-wf", name="Parent",
            nodes=[
                WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
                WorkflowNode(id="wf-node", name="CallChild", node_type=NodeType.WORKFLOW,
                    config={
                        "workflow_id": "child-wf",
                        "input_mapping": {"extra": "overridden_value"},
                        "on_failure": "fail",
                    }),
            ],
            edges=[WorkflowEdge(source_node_id="trigger", target_node_id="wf-node")],
        )
        child_wf = _child_workflow()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run", workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={"name": "Alice", "extra": "original"},
            context=RunContext(
                variables={"input": {"name": "Alice", "extra": "original"}},
                topology=topology,
            ),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs["wf-node"]
        child_run = run_repo.get_by_id(child_run_id)

        # Parent's "name" should be forwarded, "extra" overridden by input_binding
        assert child_run.input_data["name"] == "Alice"
        assert child_run.input_data["extra"] == "overridden_value"

    @pytest.mark.asyncio
    async def test_child_receives_parent_input_when_no_binding(self):
        """When input_binding is empty, child should still receive parent input."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()

        parent_wf = _parent_workflow()  # input_binding is {}
        child_wf = _child_workflow()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run", workflow_id="parent-wf",
            status=RunStatus.PENDING,
            input_data={"name": "Bob"},
            context=RunContext(
                variables={"input": {"name": "Bob"}},
                topology=topology,
            ),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)

        parent_run = run_repo.get_by_id("parent-run")
        child_run_id = parent_run.context.child_runs["wf-node"]
        child_run = run_repo.get_by_id(child_run_id)

        assert child_run.input_data["name"] == "Bob"


# ---------------------------------------------------------------------------
# ChildRunStarted event ordering
# ---------------------------------------------------------------------------

class TestChildRunStartedEventOrdering:

    @pytest.mark.asyncio
    async def test_child_run_started_emitted_before_child_entry_nodes(self):
        """ChildRunStarted must be emitted BEFORE child entry nodes are processed,
        so the frontend can subscribe to run:{child_run_id} in time."""
        workflow_repo = MemoryWorkflowRepository()
        run_repo = MemoryRunRepository()
        task_repo = MemoryTaskRepository()
        event_pub = DummyEventPublisher()

        parent_wf = _parent_workflow()
        child_wf = _child_workflow()
        workflow_repo.save(parent_wf)
        workflow_repo.save(child_wf)

        engine = _make_engine(workflow_repo, run_repo, task_repo, event_pub)

        topology = GraphTopology.from_workflow(parent_wf)
        parent_run = WorkflowRun(
            id="parent-run", workflow_id="parent-wf",
            status=RunStatus.PENDING, input_data={},
            context=RunContext(variables={"input": {}}, topology=topology),
        )
        run_repo.save(parent_run)

        tf = TaskFactory()
        await engine.handle_entry_nodes(topology.entry_nodes, parent_run, parent_wf, topology, tf)
        parent_run.status = RunStatus.RUNNING
        run_repo.save(parent_run)
        await engine.check_run_completion(parent_run, parent_wf)
        await engine.drain_events()  # drain EventCollector to event_pub

        # ChildRunStarted should appear before any child node.dispatched events
        event_topics = [t for t, _ in event_pub.events]
        child_started_idx = event_topics.index("child_run.started")

        # Find the first child task event (node.dispatched for child-trigger)
        child_run_id = run_repo.get_by_id("parent-run").context.child_runs["wf-node"]
        child_task_indices = [
            i for i, (t, e) in enumerate(event_pub.events)
            if t == "node.dispatched" and hasattr(e, "run_id") and e.run_id == child_run_id
        ]
        if child_task_indices:
            assert child_started_idx < child_task_indices[0], \
                "ChildRunStarted should be emitted before child task events"
