"""Tests for ResumePendingTaskUseCase."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects import RunStatus, TaskStatus, NodeType
from app.layer2_application.orchestration.successor_processor import ProcessingResult
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskUseCase,
    ResumePendingTaskCommand,
)
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine


@pytest.fixture
def sample_workflow_with_task():
    return Workflow(
        id="wf-1",
        name="Test Workflow",
        nodes=[
            WorkflowNode(id="n1", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n2", name="Task1", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="n1", target_node_id="n2"),
        ],
    )


class _SpyEngine(IOrchestrationEngine):
    """Minimal engine spy to capture emit_* calls without full orchestration."""

    def __init__(self, workflow_repo=None):
        self.dispatched_calls: list = []
        self.resumed_calls: list = []
        self.pending_node_completed_calls: list = []
        self.run_resumed_calls: list = []
        self.skipped_calls: list = []
        self._workflow_repo = workflow_repo
        # Pending-node resume enqueues a ResumeTaskAction for async successor
        # dispatch; mirror the real engine's queue so the resume path completes.
        self._queue = MagicMock()
        self._queue.enqueue = AsyncMock()

    def load_workflow_for_run(self, run):
        # Tests using the spy don't go through snapshot capture, so we
        # short-circuit the snapshot loader and read the workflow off
        # the live repo when one is wired in. Returns ``None`` when no
        # repo was provided (legacy callers that don't exercise this
        # path).
        if self._workflow_repo is None:
            return None
        return self._workflow_repo.get_by_id(run.workflow_id)

    def emit_node_dispatched(self, task, run, node=None, *, attempt=1, dispatch_reason=""):
        self.dispatched_calls.append((task, run, node))
        if dispatch_reason == "resume":
            self.resumed_calls.append((task, run))

    def emit_pending_node_completed(self, task, run, node, action="confirm"):
        self.pending_node_completed_calls.append((task, run, node, action))

    def emit_run_status(self, run, status, *, input_data=None, output_data=None,
                        error="", failed_task_id="", cancelled_by="", reason=""):
        if status == "resumed":
            self.run_resumed_calls.append(run)

    def emit_node_cancelled(self, run, *, task=None, node_id="", cancelled_by="", reason=""):
        self.skipped_calls.append((run, node_id, reason))

    async def process_successors(self, task, run, workflow, topology):
        return ProcessingResult()

    async def process_and_dispatch_successors(self, completed_task, run, workflow, topology, test_mode=None):
        return ProcessingResult()

    async def dispatch_or_handle_task(self, task, run, workflow, topology):
        pass

    async def check_run_completion(self, run, workflow):
        pass

    async def drain_events(self):
        pass

    async def drain_events(self):
        pass

    async def maybe_enqueue_loop_iteration(self, task, run):
        pass

    def publish_events(self, events):
        pass


def _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher=None, engine=None):
    spy = engine or _SpyEngine(workflow_repo=workflow_repo)
    uc = ResumePendingTaskUseCase(
        logger=logger,
        run_repo=run_repo,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        stage_recorder=stage_recorder,
        orchestration_engine=spy,
    )
    return uc, spy


@pytest.mark.asyncio
async def test_resume_success(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, sample_workflow_with_task
):
    workflow_repo.save(sample_workflow_with_task)
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="n2", status=TaskStatus.PENDING, worker_type="w1",
                resolved_input={"key": "value"})
    task_repo.save(task)

    uc, spy = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))

    assert result.resumed is True
    assert result.status == "RUNNING"
    assert result.task_id == "task-1"
    assert result.error == ""

    saved_task = task_repo.get_by_id("task-1")
    assert saved_task.status == TaskStatus.RUNNING
    assert saved_task.started_at is not None

    # Verify dispatch event was emitted via engine
    assert len(spy.dispatched_calls) == 1
    dispatched_task = spy.dispatched_calls[0][0]
    assert dispatched_task.id == "task-1"
    assert dispatched_task.run_id == "run-1"

    # Verify stage was recorded
    assert len(stage_recorder.records) == 1
    assert stage_recorder.records[0]["event_type"] == "task.resumed"


@pytest.mark.asyncio
async def test_resume_with_updated_file_id(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, sample_workflow_with_task
):
    workflow_repo.save(sample_workflow_with_task)
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="n2", status=TaskStatus.PENDING, worker_type="w1",
                resolved_input={"file_id": "old-file", "other_key": "keep"})
    task_repo.save(task)

    uc, spy = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"file_id": "new-file-after-edit"},
    ))

    assert result.resumed is True

    saved_task = task_repo.get_by_id("task-1")
    assert saved_task.resolved_input["file_id"] == "new-file-after-edit"
    assert saved_task.resolved_input["other_key"] == "keep"

    assert len(spy.dispatched_calls) == 1
    assert spy.dispatched_calls[0][0].resolved_input["file_id"] == "new-file-after-edit"


@pytest.mark.asyncio
async def test_resume_run_not_found(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher
):
    uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="no-run", task_id="task-1"))

    assert result.resumed is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_resume_task_not_found(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher
):
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)

    uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="no-task"))

    assert result.resumed is False
    assert "not found" in result.error


@pytest.mark.asyncio
async def test_resume_task_not_pending(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, sample_workflow_with_task
):
    workflow_repo.save(sample_workflow_with_task)
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="n2", status=TaskStatus.RUNNING, worker_type="w1")
    task_repo.save(task)

    uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))

    assert result.resumed is False
    assert result.error_code == "INVALID_TASK_STATUS"


@pytest.mark.asyncio
async def test_resume_run_not_running(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher
):
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.PAUSED)
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="n2", status=TaskStatus.PENDING)
    task_repo.save(task)

    uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))

    assert result.resumed is False
    assert result.error_code == "INVALID_RUN_STATUS"


@pytest.mark.asyncio
async def test_resume_task_run_mismatch(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher
):
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-other", node_id="n2", status=TaskStatus.PENDING)
    task_repo.save(task)

    uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))

    assert result.resumed is False
    assert "does not belong" in result.error


@pytest.mark.asyncio
async def test_resume_empty_updated_input(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, sample_workflow_with_task
):
    workflow_repo.save(sample_workflow_with_task)
    run = WorkflowRun(id="run-1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="n2", status=TaskStatus.PENDING, worker_type="w1",
                resolved_input={"original": "data"})
    task_repo.save(task)

    uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1", updated_input={}))

    assert result.resumed is True
    assert result.status == "RUNNING"

    saved_task = task_repo.get_by_id("task-1")
    assert saved_task.resolved_input == {"original": "data"}


# ────────────── INPUT / HUMAN_ACTION resume tests ──────────────


def _mock_orchestration_engine(workflow_repo=None):
    engine = MagicMock()
    engine.process_successors = AsyncMock(return_value=ProcessingResult())
    engine.process_and_dispatch_successors = AsyncMock(return_value=ProcessingResult())
    engine.check_run_completion = AsyncMock()
    engine.publish_events = MagicMock()
    engine.drain_events = AsyncMock()
    engine.drain_events = AsyncMock()
    engine.maybe_enqueue_loop_iteration = AsyncMock()
    engine._queue = MagicMock()
    engine._queue.enqueue = AsyncMock()
    # Phase B+C: use cases now resolve workflow via
    # ``engine.load_workflow_for_run`` instead of the live workflow_repo.
    # Wire a real lookup against the test's workflow_repo so resume paths
    # work without each test having to capture a snapshot.
    if workflow_repo is not None:
        engine.load_workflow_for_run = lambda run: workflow_repo.get_by_id(run.workflow_id)
    else:
        engine.load_workflow_for_run = lambda run: None
    # Phase 3b.2b: resume_pending_task now routes pending-node output
    # through ``apply_state_updates_as_files`` (emits NodeOutputProduced
    # for projector rebuild). The real engine no longer mutates the
    # aggregate; mirror the mutation here so existing assertions on
    # ``run.context.variables[...]`` continue to work. Alias propagation
    # included to match the helper's behavior.
    async def _apply(
        state_updates, run, workflow,
        *, source_node_id="", causation_id="",
        output_port="", taken_edge_ports=None, attempt_id="",
    ):
        for key, value in (state_updates or {}).items():
            run.context.variables[key] = value
            try:
                node = workflow.get_node(key)
                if node and node.name and node.name != key:
                    run.context.variables[node.name] = value
            except Exception:
                pass
    engine.apply_state_updates_as_files = _apply
    return engine


def _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine):
    return ResumePendingTaskUseCase(
        logger=logger,
        run_repo=run_repo,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        stage_recorder=stage_recorder,
        orchestration_engine=engine,
    )


@pytest.fixture
def workflow_with_input():
    return Workflow(
        id="wf-input",
        name="Input Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="input-1", name="Collect Data", node_type=NodeType.INPUT, config={
                "input_schema": [{"key": "name", "type": "string"}],
            }),
            WorkflowNode(id="task-after", name="Process", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="input-1"),
            WorkflowEdge(source_node_id="input-1", target_node_id="task-after"),
        ],
    )


@pytest.fixture
def workflow_with_human_action():
    return Workflow(
        id="wf-ha",
        name="Human Action Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="ha-1", name="Manager Approval", node_type=NodeType.HUMAN_ACTION, config={
                "instruction": "Approve or reject the PO.",
            }),
            WorkflowNode(id="approved-task", name="Send to ERP", node_type=NodeType.TASK, worker_type="w1"),
            WorkflowNode(id="rejected-task", name="Notify Requestor", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="ha-1"),
            WorkflowEdge(source_node_id="ha-1", target_node_id="approved-task", condition="confirm"),
            WorkflowEdge(source_node_id="ha-1", target_node_id="rejected-task", condition="reject"),
        ],
    )


def _make_topology(workflow):
    """Build GraphTopology from a workflow for testing."""
    adjacency: dict[str, list[str]] = {}
    predecessors: dict[str, list[str]] = {}
    edge_map: dict[str, list[WorkflowEdge]] = {}
    for edge in workflow.edges:
        adjacency.setdefault(edge.source_node_id, []).append(edge.target_node_id)
        predecessors.setdefault(edge.target_node_id, []).append(edge.source_node_id)
        edge_map.setdefault(edge.source_node_id, []).append(edge)
    entry_nodes = [n.id for n in workflow.nodes if n.id not in predecessors]
    leaf_nodes = [n.id for n in workflow.nodes if n.id not in adjacency]
    return GraphTopology(
        adjacency=adjacency,
        predecessors=predecessors,
        edge_map=edge_map,
        entry_nodes=entry_nodes,
        leaf_nodes=leaf_nodes,
    )


@pytest.mark.asyncio
async def test_resume_input_node_completes_and_processes_successors(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_input
):
    workflow_repo.save(workflow_with_input)
    topology = _make_topology(workflow_with_input)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-input", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="input-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"name": "Alice"},
    ))

    assert result.resumed is True
    assert result.status == "COMPLETED"

    saved_task = task_repo.get_by_id("task-1")
    assert saved_task.status == TaskStatus.COMPLETED

    # Queue mode: use case enqueues a ResumeTaskAction for the worker to process.
    engine._queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_resume_input_node_stores_data_in_context(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_input
):
    workflow_repo.save(workflow_with_input)
    topology = _make_topology(workflow_with_input)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-input", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="input-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"name": "Alice", "email": "alice@example.com"},
    ))

    saved_run = run_repo.get_by_id("run-1")
    assert "input-1" in saved_run.context.variables
    assert saved_run.context.variables["input-1"]["name"] == "Alice"
    assert "input-1" in saved_run.context.completed_nodes

    # Also aliased by node name
    assert "Collect Data" in saved_run.context.variables

    # Verify emit_pending_node_completed was called on the engine
    engine.emit_pending_node_completed.assert_called_once()
    call_args = engine.emit_pending_node_completed.call_args
    assert call_args[0][0].id == "task-1"   # task
    assert call_args[0][1].id == "run-1"    # run


@pytest.mark.asyncio
async def test_resume_human_action_confirm_routes_correctly(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_human_action
):
    workflow_repo.save(workflow_with_human_action)
    topology = _make_topology(workflow_with_human_action)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-ha", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="ha-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"_action": "confirm", "comments": "Looks good"},
    ))

    assert result.resumed is True
    assert result.status == "COMPLETED"

    saved_run = run_repo.get_by_id("run-1")
    # Confirm path taken: rejected-task should be skipped
    assert "rejected-task" in saved_run.context.skipped_nodes
    assert "approved-task" not in saved_run.context.skipped_nodes

    # Verify action stored in context
    assert saved_run.context.variables["ha-1"]["_action"] == "confirm"
    assert saved_run.context.variables["ha-1"]["comments"] == "Looks good"

    # Verify emit_pending_node_completed was called with action=confirm
    engine.emit_pending_node_completed.assert_called_once()
    call_args = engine.emit_pending_node_completed.call_args
    assert call_args[1].get("action", call_args[0][3] if len(call_args[0]) > 3 else "confirm") == "confirm"


@pytest.mark.asyncio
async def test_resume_human_action_reject_skips_confirm_branch(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_human_action
):
    workflow_repo.save(workflow_with_human_action)
    topology = _make_topology(workflow_with_human_action)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-ha", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="ha-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"_action": "reject", "reason": "Budget exceeded"},
    ))

    assert result.resumed is True

    saved_run = run_repo.get_by_id("run-1")
    # Reject path taken: approved-task should be skipped
    assert "approved-task" in saved_run.context.skipped_nodes
    assert "rejected-task" not in saved_run.context.skipped_nodes


@pytest.mark.asyncio
async def test_resume_human_action_default_action_is_confirm(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_human_action
):
    workflow_repo.save(workflow_with_human_action)
    topology = _make_topology(workflow_with_human_action)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-ha", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="ha-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    # No _action in updated_input — should default to "confirm"
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"comments": "OK"},
    ))

    assert result.resumed is True

    saved_run = run_repo.get_by_id("run-1")
    assert saved_run.context.variables["ha-1"]["_action"] == "confirm"
    assert "rejected-task" in saved_run.context.skipped_nodes


@pytest.fixture
def workflow_with_human_action_ports():
    """Workflow where edges use source_port_id (no condition) — matches real FE behaviour."""
    return Workflow(
        id="wf-ha-ports",
        name="Human Action Workflow (ports)",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="ha-1", name="Manager Approval", node_type=NodeType.HUMAN_ACTION, config={
                "instruction": "Approve or reject the PO.",
            }),
            WorkflowNode(id="approved-task", name="Send to ERP", node_type=NodeType.TASK, worker_type="w1"),
            WorkflowNode(id="rejected-task", name="Notify Requestor", node_type=NodeType.TASK, worker_type="w2"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="ha-1"),
            WorkflowEdge(source_node_id="ha-1", target_node_id="approved-task", source_port_id="confirm"),
            WorkflowEdge(source_node_id="ha-1", target_node_id="rejected-task", source_port_id="reject"),
        ],
    )


@pytest.mark.asyncio
async def test_resume_human_action_infer_reject_from_input_key(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_human_action_ports
):
    """When FE sends {"reject": true} without _action, backend should infer action=reject."""
    workflow_repo.save(workflow_with_human_action_ports)
    topology = _make_topology(workflow_with_human_action_ports)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-ha-ports", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="ha-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    # FE sends {"reject": true} without _action
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"reject": True},
    ))

    assert result.resumed is True
    saved_run = run_repo.get_by_id("run-1")
    assert saved_run.context.variables["ha-1"]["_action"] == "reject"
    assert "approved-task" in saved_run.context.skipped_nodes
    assert "rejected-task" not in saved_run.context.skipped_nodes


@pytest.mark.asyncio
async def test_resume_human_action_infer_confirm_when_no_action_key(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, workflow_with_human_action_ports
):
    """When FE sends data without _action and without reject key, default to confirm."""
    workflow_repo.save(workflow_with_human_action_ports)
    topology = _make_topology(workflow_with_human_action_ports)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-ha-ports", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="ha-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_engine(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine)
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"comments": "All good"},
    ))

    assert result.resumed is True
    saved_run = run_repo.get_by_id("run-1")
    assert saved_run.context.variables["ha-1"]["_action"] == "confirm"
    assert "rejected-task" in saved_run.context.skipped_nodes
    assert "approved-task" not in saved_run.context.skipped_nodes


# ────────────── DATA_EDIT resume tests ──────────────


@pytest.fixture
def workflow_with_data_edit():
    return Workflow(
        id="wf-de",
        name="Data Edit Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="data-1", name="Edit Orders", node_type=NodeType.DATA_EDIT,
                input_binding={"file_id": "{{$src.file_id}}"},
            ),
            WorkflowNode(id="task-after", name="Process", node_type=NodeType.TASK, worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="data-1"),
            WorkflowEdge(source_node_id="data-1", target_node_id="task-after"),
        ],
    )


class _StubViewStore:
    """Minimal stub for IRunContextViewStore.get(run_id) — enough to feed
    the materialize/swap logic in ``_resume_human_interaction_task``."""

    def __init__(self, view):
        self._view = view

    def get(self, run_id):
        return self._view


@pytest.mark.asyncio
async def test_resume_data_edit_uses_post_patch_working_ref_from_view(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
    workflow_with_data_edit,
):
    """When DATA_EDIT resumes, the file_ref in ``view.variables[node.id]``
    (carrying the post-PATCH file_id) must replace ``task.resolved_input``
    so the edited file_id flows into ``apply_state_updates_as_files`` —
    not the dispatch-time ref the handler stamped on the task."""
    from app.layer1_domain.entities.data_reference import build_file_ref, add_artifact

    workflow_repo.save(workflow_with_data_edit)
    topology = _make_topology(workflow_with_data_edit)

    # Dispatch-time working ref: table_data points at the upstream file_id.
    dispatch_ref = build_file_ref(file_id="", artifact_id="__root__")
    dispatch_ref["artifact_type"] = "object_field"
    add_artifact(
        dispatch_ref, file_id="f-old-upstream", name="table_data",
        artifact_id="table_data", artifact_type="array_field",
    )

    # Post-PATCH working ref (what the projector stored after the user
    # edited a row): same shape, different file_id.
    post_patch_ref = build_file_ref(file_id="", artifact_id="__root__")
    post_patch_ref["artifact_type"] = "object_field"
    add_artifact(
        post_patch_ref, file_id="f-new-edited", name="table_data",
        artifact_id="table_data", artifact_type="array_field",
    )
    post_patch_ref["version"] = 1

    run = WorkflowRun(
        id="run-de", workflow_id="wf-de", status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology, completed_nodes={"trigger"},
            variables={"data-1": post_patch_ref},
        ),
    )
    run_repo.save(run)

    task = Task(
        id="task-de", run_id="run-de", node_id="data-1",
        status=TaskStatus.PENDING, resolved_input=dispatch_ref,
        output_metadata={"node_type": "DATA_EDIT"},
    )
    task_repo.save(task)

    # View store carries the post-PATCH ref under variables[data-1].
    class _View:
        status = RunStatus.RUNNING.value
        variables = run.context.variables
    view_store = _StubViewStore(_View())

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = ResumePendingTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
        run_context_view_store=view_store,
    )

    # Capture apply_state_updates_as_files to inspect what file_ref the
    # node output carries.
    captured = {}
    async def _capture(state_updates, run_, workflow, **kwargs):
        captured["state_updates"] = state_updates
        for k, v in (state_updates or {}).items():
            run_.context.variables[k] = v
    engine.apply_state_updates_as_files = _capture

    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-de", task_id="task-de",
    ))

    assert result.resumed is True
    assert "state_updates" in captured, "engine must receive a state_updates payload"
    output_ref = captured["state_updates"]["data-1"]
    # Post-PATCH file_id, not dispatch-time, must propagate.
    output_artifacts = output_ref.get("artifacts") or []
    assert len(output_artifacts) == 1
    assert output_artifacts[0]["file_id"] == "f-new-edited", (
        "DATA_EDIT submit must surface the post-PATCH working ref to "
        "apply_state_updates_as_files, not the dispatch-time ref"
    )

    # task.resolved_input must have been swapped to the file_ref shape
    # (not materialized into a dict like HUMAN_ACTION/FILE_UPLOAD).
    saved_task = task_repo.get_by_id("task-de")
    assert saved_task.resolved_input.get("__file_ref") is True

    # input_file_id roundtrip is skipped for DATA_EDIT — the file_ref tree
    # is the input artifact, no need to re-store it as a single-row CSV.
    assert "input_file_id" not in (saved_task.output_metadata or {})


@pytest.mark.asyncio
async def test_resume_input_node_materializes_post_patch_working_ref_from_view(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
    workflow_with_input,
):
    """When an INPUT node resumes via POST /submit (no body), the
    file-backed working copy in ``view.variables[node.id]`` — carrying
    the post-PATCH file_id — must be materialized into
    ``task.resolved_input`` so the user's form data flows into the node
    output. Without the step-0 INPUT branch the stale, empty
    dispatch-time provisioned ref would be submitted instead."""
    from app.layer1_domain.entities.data_reference import build_file_ref

    workflow_repo.save(workflow_with_input)
    topology = _make_topology(workflow_with_input)

    # Dispatch-time provisioned ref (empty root CSV) vs. the post-PATCH
    # ref the projector stored after the user PATCHed their form values.
    dispatch_ref = build_file_ref(file_id="f-empty-provisioned")
    post_patch_ref = build_file_ref(file_id="f-after-patch")
    post_patch_ref["version"] = 1

    run = WorkflowRun(
        id="run-in", workflow_id="wf-input", status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology, completed_nodes={"trigger"},
            variables={"input-1": post_patch_ref},
        ),
    )
    run_repo.save(run)

    task = Task(
        id="task-in", run_id="run-in", node_id="input-1",
        status=TaskStatus.PENDING, resolved_input=dispatch_ref,
        output_metadata={
            "node_type": "INPUT",
            "input_schema": [{"key": "name", "type": "string"}],
        },
    )
    task_repo.save(task)

    class _View:
        status = RunStatus.RUNNING.value
        variables = run.context.variables
    view_store = _StubViewStore(_View())

    # Data store: materialize the post-PATCH ref into the user's form
    # data; store_data echoes a file_ref so the rest of the flow runs.
    data_store = MagicMock()
    # SA-1942 — resume now routes the working-copy materialize through the
    # size-guarded wrapper.
    data_store.materialize_guarded = AsyncMock(return_value={"name": "Alice"})
    data_store.store_data = AsyncMock(
        return_value=build_file_ref(file_id="f-stored"),
    )

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    captured = {}
    async def _capture(state_updates, run_, workflow, **kwargs):
        captured["state_updates"] = state_updates
        for k, v in (state_updates or {}).items():
            run_.context.variables[k] = v
    engine.apply_state_updates_as_files = _capture

    uc = ResumePendingTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
        run_context_view_store=view_store,
        data_store_service=data_store,
    )

    # No updated_input — mirrors the real POST /submit path.
    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-in", task_id="task-in",
    ))

    assert result.resumed is True
    assert result.status == "COMPLETED"

    # The post-PATCH working ref must have been materialized (size-guarded),
    # not skipped.
    data_store.materialize_guarded.assert_awaited_once_with(post_patch_ref)

    # The node output must be built from the materialized form data —
    # ``store_data(node.id, ...)`` is only reached when resolved_input is
    # the plain dict, not the (empty) dispatch-time file_ref.
    node_output_calls = [
        c for c in data_store.store_data.call_args_list
        if c.args and c.args[0] == "input-1"
    ]
    assert node_output_calls, (
        "INPUT submit must materialize the post-PATCH working copy and "
        "store the user's form data as the node output"
    )
    assert node_output_calls[0].args[1] == {"name": "Alice"}


@pytest.mark.asyncio
async def test_resume_input_node_oversized_working_ref_degrades_gracefully(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
    workflow_with_input,
):
    """SA-1942 — if the HITL working copy is too large to materialize (guard
    trips), resume must NOT crash: the existing ``except`` turns the
    MaterializeTooLargeError into a graceful skip (``resolved_input`` is left as
    the dispatch-time ref, submitted-field extraction bypassed), and the resume
    still completes."""
    from app.layer1_domain.entities.data_reference import build_file_ref
    from app.layer2_application.data.data_store_service import MaterializeTooLargeError

    workflow_repo.save(workflow_with_input)
    topology = _make_topology(workflow_with_input)

    dispatch_ref = build_file_ref(file_id="f-empty-provisioned")
    post_patch_ref = build_file_ref(file_id="f-after-patch")
    post_patch_ref["version"] = 1

    run = WorkflowRun(
        id="run-in", workflow_id="wf-input", status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology, completed_nodes={"trigger"},
            variables={"input-1": post_patch_ref},
        ),
    )
    run_repo.save(run)
    task = Task(
        id="task-in", run_id="run-in", node_id="input-1",
        status=TaskStatus.PENDING, resolved_input=dispatch_ref,
        output_metadata={
            "node_type": "INPUT",
            "input_schema": [{"key": "name", "type": "string"}],
        },
    )
    task_repo.save(task)

    class _View:
        status = RunStatus.RUNNING.value
        variables = run.context.variables
    view_store = _StubViewStore(_View())

    data_store = MagicMock()
    # The guard trips on the oversized working copy.
    data_store.materialize_guarded = AsyncMock(
        side_effect=MaterializeTooLargeError(post_patch_ref, 999_999_999, 1),
    )
    data_store.store_data = AsyncMock(return_value=build_file_ref(file_id="f-stored"))

    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    async def _capture(state_updates, run_, workflow, **kwargs):
        for k, v in (state_updates or {}).items():
            run_.context.variables[k] = v
    engine.apply_state_updates_as_files = _capture

    uc = ResumePendingTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
        run_context_view_store=view_store,
        data_store_service=data_store,
    )

    # Must not raise despite the guard tripping.
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-in", task_id="task-in"))
    assert result.resumed is True
    data_store.materialize_guarded.assert_awaited_once_with(post_patch_ref)
    # resolved_input was NOT overwritten with the (unmaterializable) working copy.
    assert task.resolved_input == dispatch_ref


# ────────────── DATA_EDIT artifact_key alias tests ──────────────


def _build_usecase_with_data_store(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher, engine, data_store,
):
    return ResumePendingTaskUseCase(
        logger=logger,
        run_repo=run_repo,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
        stage_recorder=stage_recorder,
        event_publisher=event_publisher,
        orchestration_engine=engine,
        data_store_service=data_store,
    )


def _data_edit_workflow(artifact_key: str) -> Workflow:
    """TRIGGER → DATA_EDIT (with FE-style nested input_binding carrying artifact_key)."""
    return Workflow(
        id="wf-de",
        name="Data edit workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="editor",
                name="Edit",
                node_type=NodeType.DATA_EDIT,
                input_binding={
                    "data_edit_tabs": {
                        "main": {
                            "file_id": "",
                            "artifact_key": artifact_key,
                            "data_mode": "csv_single",
                        },
                        "settings_tab": {"readonly": False},
                    },
                },
            ),
        ],
        edges=[WorkflowEdge(source_node_id="trigger", target_node_id="editor")],
    )


@pytest.mark.asyncio
async def test_data_edit_resume_aliases_table_data_under_artifact_key(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
):
    """After resuming a DATA_EDIT task, $artifacts.<artifact_key> should resolve to the
    same file_id as $artifacts.table_data (alias semantics)."""
    from app.layer2_application.data.data_store_service import DataStoreService
    from app.layer2_application.data.csv_converter import CsvConverter
    from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient

    artifact_key = "my_edited_data"
    workflow = _data_edit_workflow(artifact_key)
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-de", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="editor", status=TaskStatus.PENDING)
    task_repo.save(task)

    data_store = DataStoreService(MockFileServiceClient(), CsvConverter())
    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_data_store(
        logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
        engine, data_store,
    )

    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-1", task_id="task-1",
        updated_input={"table_data": [
            {"name": "Alice", "qty": "5"},
            {"name": "Bob", "qty": "3"},
        ]},
    ))

    assert result.resumed is True
    saved_run = run_repo.get_by_id("run-1")
    artifacts = saved_run.context.variables.get("artifacts") or {}

    # Both names should be present in the global $artifacts namespace
    assert "table_data" in artifacts, f"$artifacts.table_data missing; have {list(artifacts)}"
    assert artifact_key in artifacts, f"$artifacts.{artifact_key} missing; have {list(artifacts)}"

    # And they should point at the same file_id (alias, not duplicate upload).
    # The alias entry is named after artifact_key, so its `filename` differs;
    # the alias is by file_id (see ResumePendingTaskUseCase DATA_EDIT aliasing
    # via add_artifact), not a byte-for-byte copy of the table_data entry.
    assert artifacts[artifact_key]["file_id"] == artifacts["table_data"]["file_id"]
    assert artifacts["table_data"]["file_id"]  # non-empty file_id


@pytest.mark.asyncio
async def test_data_edit_resume_no_artifact_key_leaves_table_data_only(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
):
    """When artifact_key is empty/absent, only the auto-named table_data artifact
    is registered — current behaviour is preserved (no regression, no default name)."""
    from app.layer2_application.data.data_store_service import DataStoreService
    from app.layer2_application.data.csv_converter import CsvConverter
    from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient

    workflow = _data_edit_workflow(artifact_key="")  # explicitly empty
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-2", workflow_id="wf-de", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-2", run_id="run-2", node_id="editor", status=TaskStatus.PENDING)
    task_repo.save(task)

    data_store = DataStoreService(MockFileServiceClient(), CsvConverter())
    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_data_store(
        logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
        engine, data_store,
    )

    await uc.execute(ResumePendingTaskCommand(
        run_id="run-2", task_id="task-2",
        updated_input={"table_data": [{"name": "Alice", "qty": "5"}]},
    ))

    saved_run = run_repo.get_by_id("run-2")
    artifacts = saved_run.context.variables.get("artifacts") or {}
    assert "table_data" in artifacts
    # No default key invented when artifact_key is empty
    assert list(artifacts.keys()) == ["table_data"]


def _template_data_edit_workflow(artifact_key: str) -> Workflow:
    """TRIGGER → TEMPLATE_DATA_EDIT carrying artifact_key as a flat declared
    config field (no form_tabs_key, unlike DATA_EDIT's nested input_binding)."""
    return Workflow(
        id="wf-tde",
        name="Template data edit workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="editor",
                name="Edit",
                node_type=NodeType.TEMPLATE_DATA_EDIT,
                config={"artifact_key": artifact_key},
            ),
        ],
        edges=[WorkflowEdge(source_node_id="trigger", target_node_id="editor")],
    )


@pytest.mark.asyncio
async def test_template_data_edit_resume_aliases_under_config_artifact_key(
    logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
):
    """TEMPLATE_DATA_EDIT carries artifact_key in node.config (it declares no
    form_tabs_key), so resume must alias table_data under it just like DATA_EDIT
    reads it from the nested input_binding."""
    from app.layer2_application.data.data_store_service import DataStoreService
    from app.layer2_application.data.csv_converter import CsvConverter
    from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient

    artifact_key = "my_template_data"
    workflow = _template_data_edit_workflow(artifact_key)
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-3", workflow_id="wf-tde", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-3", run_id="run-3", node_id="editor", status=TaskStatus.PENDING)
    task_repo.save(task)

    data_store = DataStoreService(MockFileServiceClient(), CsvConverter())
    engine = _mock_orchestration_engine(workflow_repo=workflow_repo)
    uc = _build_usecase_with_data_store(
        logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
        engine, data_store,
    )

    result = await uc.execute(ResumePendingTaskCommand(
        run_id="run-3", task_id="task-3",
        updated_input={"table_data": [{"name": "Alice", "qty": "5"}]},
    ))

    assert result.resumed is True
    saved_run = run_repo.get_by_id("run-3")
    artifacts = saved_run.context.variables.get("artifacts") or {}
    assert "table_data" in artifacts
    assert artifact_key in artifacts, f"$artifacts.{artifact_key} missing; have {list(artifacts)}"
    assert artifacts[artifact_key]["file_id"] == artifacts["table_data"]["file_id"]


# ---------------------------------------------------------------------------
# Re-opening historical FAILED runs that were swept by the (now-removed)
# child workflow timeout sweeper.
# ---------------------------------------------------------------------------


def _ha_workflow():
    return Workflow(
        id="wf-ha",
        name="HA workflow",
        nodes=[
            WorkflowNode(
                id="ha",
                name="ApprovalStep",
                node_type=NodeType.HUMAN_ACTION,
                config={"instruction": "Approve", "input_schema": []},
            ),
        ],
        edges=[],
    )


class TestResumeFromFailedRun:
    """Cover the recovery path for runs left FAILED by the historical
    SweepTimedOutChildRunsUseCase. After deletion of the sweep, those runs
    must be re-openable by replaying ResumePendingTaskUseCase.
    """

    @pytest.mark.asyncio
    async def test_running_run_pending_task_still_works(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
    ):
        """Regression: the ordinary RUNNING + PENDING flow is unchanged."""
        workflow_repo.save(_ha_workflow())
        run = WorkflowRun(id="run-1", workflow_id="wf-ha", status=RunStatus.RUNNING,
                          context=RunContext(topology=GraphTopology(entry_nodes=["ha"])))
        run_repo.save(run)
        task = Task(id="t-1", run_id="run-1", node_id="ha", status=TaskStatus.PENDING)
        task_repo.save(task)

        uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
        result = await uc.execute(ResumePendingTaskCommand(
            run_id="run-1", task_id="t-1", updated_input={"_action": "confirm"},
        ))
        assert result.resumed is True
        saved_task = task_repo.get_by_id("t-1")
        assert saved_task.status == TaskStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_failed_run_with_swept_task_can_be_resumed(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
    ):
        """A child run previously FAILED by the sweeper, with its HA task
        CANCELLED + tagged ``parent_timeout``, can be re-opened.

        After resume, the run is back to a terminal/non-FAILED state and the
        HA task has been processed (status COMPLETED for HA confirm).
        """
        workflow_repo.save(_ha_workflow())
        run = WorkflowRun(
            id="child-run", workflow_id="wf-ha",
            status=RunStatus.FAILED,
            error="Child workflow timed out after 1000ms",
            completed_at="2025-01-01T00:00:00+00:00",
            context=RunContext(topology=GraphTopology(entry_nodes=["ha"])),
        )
        run_repo.save(run)
        task = Task(
            id="t-ha", run_id="child-run", node_id="ha",
            status=TaskStatus.CANCELLED,
            output_metadata={"reason": "parent_timeout"},
            completed_at="2025-01-01T00:00:00+00:00",
        )
        task_repo.save(task)

        uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
        result = await uc.execute(ResumePendingTaskCommand(
            run_id="child-run", task_id="t-ha", updated_input={"_action": "confirm"},
        ))

        assert result.resumed is True, f"resume failed: {result.error}"

        saved_run = run_repo.get_by_id("child-run")
        assert saved_run.status != RunStatus.FAILED, (
            f"run should no longer be FAILED, got {saved_run.status}"
        )
        assert saved_run.error in (None, "")
        assert saved_run.completed_at is None or saved_run.status == RunStatus.COMPLETED

        saved_task = task_repo.get_by_id("t-ha")
        assert saved_task.status == TaskStatus.COMPLETED
        # parent_timeout marker should have been cleared during rehydrate.
        assert (saved_task.output_metadata or {}).get("reason") != "parent_timeout"

        # A run.reopened stage should have been recorded.
        reopened_stages = [r for r in stage_recorder.records if r.get("event_type") == "run.reopened"]
        assert len(reopened_stages) == 1
        assert reopened_stages[0]["from_status"] == RunStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_failed_run_with_non_timeout_failure_is_rejected(
        self, logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher,
    ):
        """A FAILED run with a non-timeout failure (e.g. real worker error)
        must NOT be resumable through this path — only swept runs.
        """
        workflow_repo.save(_ha_workflow())
        run = WorkflowRun(
            id="bad-run", workflow_id="wf-ha",
            status=RunStatus.FAILED, error="boom",
        )
        run_repo.save(run)
        task = Task(
            id="t-bad", run_id="bad-run", node_id="ha",
            status=TaskStatus.FAILED, error="real error",
        )
        task_repo.save(task)

        uc, _ = _build_usecase(logger, run_repo, task_repo, workflow_repo, stage_recorder, event_publisher)
        result = await uc.execute(ResumePendingTaskCommand(
            run_id="bad-run", task_id="t-bad",
        ))

        assert result.resumed is False
        assert result.error_code == "INVALID_TASK_STATUS"
        # State is unchanged.
        assert run_repo.get_by_id("bad-run").status == RunStatus.FAILED
        assert task_repo.get_by_id("t-bad").status == TaskStatus.FAILED
