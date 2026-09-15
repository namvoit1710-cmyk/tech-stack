"""Tests for the WAIT node (SA-1371 S4): WaitNodeHandler + timer-driven resume.

Two halves:

1. The handler PARKS the run like an INPUT node and SCHEDULES a durable timer
   (correct ``kind`` + ``fire_at`` per ``mode``).
2. The resume path: a ``TimerFired`` for a WAIT node resumes the parked task
   (COMPLETED + successors dispatched), and is idempotent on a duplicate fire.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.events import InputRequested
from app.layer1_domain.value_objects import NodeType, RunStatus, TaskStatus
from app.layer2_application.features.create_timer.use_cases.create_timer_use_case import (
    CreateTimerCommand,
)
from app.layer2_application.features.resume_pending_task.use_cases.resume_pending_task_use_case import (
    ResumePendingTaskUseCase,
    ResumePendingTaskCommand,
)
from app.layer2_application.features.resume_run.use_cases.resume_run_use_case import (
    ResumeRunUseCase,
    ResumeRunCommand,
)
from app.layer2_application.node_handlers.wait_handler import WaitNodeHandler
from app.layer2_application.orchestration.successor_processor import ProcessingResult
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.timer.timer_fired_consumer import TimerFiredConsumer


# ─────────────────────────── fakes / fixtures ───────────────────────────


class _SpyCreateTimer:
    """Captures the CreateTimerCommand the handler issues."""

    def __init__(self):
        self.commands: list[CreateTimerCommand] = []

    async def execute(self, command: CreateTimerCommand):
        self.commands.append(command)
        return MagicMock(scheduled=True, timer_id="t-fake")


@pytest.fixture
def variable_resolver():
    return VariableResolver(strategies=[TraversalResolverStrategy()])


@pytest.fixture
def task_factory():
    return TaskFactory()


@pytest.fixture
def run():
    return WorkflowRun(id="run-1", workflow_id="wf-1", context=RunContext())


@pytest.fixture
def topology():
    return GraphTopology()


def _wait_node(node_id="wait-1", **config):
    return WorkflowNode(
        id=node_id, name="Wait", node_type=NodeType.WAIT, config=config,
    )


# ─────────────────────────── handler: park + schedule ───────────────────────────


@pytest.mark.asyncio
async def test_wait_handler_parks_run(variable_resolver, run, topology, task_factory):
    spy = _SpyCreateTimer()
    handler = WaitNodeHandler(variable_resolver, create_timer_use_case=spy)
    node = _wait_node(mode="delay", delay_seconds=60)
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])

    result = await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    # Parked exactly like an INPUT node: single PENDING task, pending mode.
    assert result.completion_mode == "pending"
    assert result.pending_task is not None
    assert len(result.tasks_to_dispatch) == 1
    task = result.tasks_to_dispatch[0]
    assert task.status == TaskStatus.PENDING
    assert task.node_id == "wait-1"
    # Emits an InputRequested with the WAIT discriminator.
    assert len(result.events) == 1
    event = result.events[0]
    assert isinstance(event, InputRequested)
    assert event.pending_kind == "wait"
    assert event.node_id == "wait-1"


@pytest.mark.asyncio
async def test_wait_handler_schedules_delay_timer(variable_resolver, run, topology, task_factory):
    spy = _SpyCreateTimer()
    handler = WaitNodeHandler(variable_resolver, create_timer_use_case=spy)
    node = _wait_node(mode="delay", delay_seconds=120)
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])

    await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    assert len(spy.commands) == 1
    cmd = spy.commands[0]
    assert cmd.run_id == "run-1"
    assert cmd.workflow_id == "wf-1"
    assert cmd.node_id == "wait-1"
    assert cmd.kind == "delay"
    # fire_at ≈ now + 120s (just assert it parses and is in the future).
    fire_at = datetime.fromisoformat(cmd.fire_at)
    assert fire_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_wait_handler_schedules_until_timer(variable_resolver, run, topology, task_factory):
    spy = _SpyCreateTimer()
    handler = WaitNodeHandler(variable_resolver, create_timer_use_case=spy)
    node = _wait_node(mode="until", until="2030-01-01T00:00:00+00:00")
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])

    await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    cmd = spy.commands[0]
    # "until" mode → kind="schedule", fire_at is the timestamp verbatim.
    assert cmd.kind == "schedule"
    assert cmd.fire_at == "2030-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_wait_handler_schedules_sla_timer(variable_resolver, run, topology, task_factory):
    spy = _SpyCreateTimer()
    handler = WaitNodeHandler(variable_resolver, create_timer_use_case=spy)
    node = _wait_node(mode="sla", sla_timeout_seconds=3600)
    workflow = Workflow(id="wf-1", name="W", nodes=[node], edges=[])

    await handler.handle(
        node=node, run=run, workflow=workflow, topology=topology,
        completed_task=None, task_factory=task_factory,
    )

    cmd = spy.commands[0]
    assert cmd.kind == "sla"
    fire_at = datetime.fromisoformat(cmd.fire_at)
    assert fire_at > datetime.now(timezone.utc)


# ─────────────────────────── resume via ResumePendingTaskUseCase ───────────────────────────


def _wait_workflow():
    return Workflow(
        id="wf-wait",
        name="Wait Workflow",
        nodes=[
            WorkflowNode(id="trigger", name="Start", node_type=NodeType.TRIGGER),
            WorkflowNode(id="wait-1", name="Sleep", node_type=NodeType.WAIT,
                         config={"mode": "delay", "delay_seconds": 60}),
            WorkflowNode(id="task-after", name="Process", node_type=NodeType.TASK,
                         worker_type="w1"),
        ],
        edges=[
            WorkflowEdge(source_node_id="trigger", target_node_id="wait-1"),
            WorkflowEdge(source_node_id="wait-1", target_node_id="task-after"),
        ],
    )


def _make_topology(workflow):
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
        adjacency=adjacency, predecessors=predecessors, edge_map=edge_map,
        entry_nodes=entry_nodes, leaf_nodes=leaf_nodes,
    )


def _mock_engine(workflow_repo):
    engine = MagicMock()
    engine.process_successors = AsyncMock(return_value=ProcessingResult())
    engine.process_and_dispatch_successors = AsyncMock(return_value=ProcessingResult())
    engine.check_run_completion = AsyncMock()
    engine.publish_events = MagicMock()
    engine.drain_events = AsyncMock()
    engine.maybe_enqueue_loop_iteration = AsyncMock()
    engine._queue = MagicMock()
    engine._queue.enqueue = AsyncMock()
    engine.load_workflow_for_run = lambda run: workflow_repo.get_by_id(run.workflow_id)

    async def _apply(state_updates, run, workflow, *, source_node_id="",
                     causation_id="", output_port="", taken_edge_ports=None,
                     attempt_id=""):
        for key, value in (state_updates or {}).items():
            run.context.variables[key] = value
    engine.apply_state_updates_as_files = _apply
    return engine


@pytest.mark.asyncio
async def test_timer_fired_resumes_wait_node_and_dispatches_successors(
    logger, run_repo, task_repo, workflow_repo, stage_recorder,
):
    workflow = _wait_workflow()
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-wait", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="wait-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_engine(workflow_repo)
    uc = ResumePendingTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
    )

    # A timer supplies no user input — resume with an empty command.
    result = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))

    assert result.resumed is True
    assert result.status == "COMPLETED"
    assert task_repo.get_by_id("task-1").status == TaskStatus.COMPLETED
    # WAIT node output stored + node marked complete → successors dispatched
    # (resume enqueues a ResumeTaskAction for the successor fan-out).
    engine._queue.enqueue.assert_called_once()
    assert "wait-1" in run_repo.get_by_id("run-1").context.completed_nodes


@pytest.mark.asyncio
async def test_duplicate_timer_fired_is_idempotent(
    logger, run_repo, task_repo, workflow_repo, stage_recorder,
):
    workflow = _wait_workflow()
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-wait", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="wait-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_engine(workflow_repo)
    uc = ResumePendingTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
    )

    first = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))
    second = await uc.execute(ResumePendingTaskCommand(run_id="run-1", task_id="task-1"))

    assert first.resumed is True
    # Second resume is a no-op — the task is no longer PENDING.
    assert second.resumed is False
    assert second.error_code  # INVALID_TASK_STATUS
    # Successor fan-out enqueued exactly once, not twice.
    engine._queue.enqueue.assert_called_once()


# ─────────────────────────── TimerFiredConsumer (full timer→resume path) ───────────────────────────


def _build_resume_run(logger, run_repo, task_repo, workflow_repo, stage_recorder, engine):
    resume_pending = ResumePendingTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
    )
    resume_run = ResumeRunUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=workflow_repo, stage_recorder=stage_recorder,
        orchestration_engine=engine,
    )
    resume_run._resume_pending_task_usecase = resume_pending
    return resume_run


def _timer_fired_msg(run_id="run-1", node_id="wait-1"):
    return {"payload": {"run_id": run_id, "node_id": node_id,
                        "timer_id": "t1", "kind": "delay"}}


@pytest.mark.asyncio
async def test_consumer_resumes_wait_node(
    logger, run_repo, task_repo, workflow_repo, stage_recorder,
):
    workflow = _wait_workflow()
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-wait", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="wait-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_engine(workflow_repo)
    resume_run = _build_resume_run(logger, run_repo, task_repo, workflow_repo, stage_recorder, engine)
    container = {"resume_run": resume_run, "run_repo": run_repo, "workflow_repo": workflow_repo}
    consumer = TimerFiredConsumer(container, broker=MagicMock())

    await consumer._handle(_timer_fired_msg())

    assert task_repo.get_by_id("task-1").status == TaskStatus.COMPLETED
    engine._queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_consumer_duplicate_timer_fired_no_double_dispatch(
    logger, run_repo, task_repo, workflow_repo, stage_recorder,
):
    workflow = _wait_workflow()
    workflow_repo.save(workflow)
    topology = _make_topology(workflow)
    run = WorkflowRun(
        id="run-1", workflow_id="wf-wait", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"trigger"}),
    )
    run_repo.save(run)
    task = Task(id="task-1", run_id="run-1", node_id="wait-1", status=TaskStatus.PENDING)
    task_repo.save(task)

    engine = _mock_engine(workflow_repo)
    resume_run = _build_resume_run(logger, run_repo, task_repo, workflow_repo, stage_recorder, engine)
    container = {"resume_run": resume_run, "run_repo": run_repo, "workflow_repo": workflow_repo}
    consumer = TimerFiredConsumer(container, broker=MagicMock())

    await consumer._handle(_timer_fired_msg())
    await consumer._handle(_timer_fired_msg())  # duplicate fire

    assert task_repo.get_by_id("task-1").status == TaskStatus.COMPLETED
    # Fan-out enqueued exactly once across both fires.
    engine._queue.enqueue.assert_called_once()


@pytest.mark.asyncio
async def test_consumer_ignores_non_wait_node(
    logger, run_repo, task_repo, workflow_repo, stage_recorder,
):
    """A timer fired against a non-WAIT node must not be resumed by this
    consumer (guard prevents mis-resuming an unrelated run)."""
    workflow = Workflow(
        id="wf-task", name="Task WF",
        nodes=[WorkflowNode(id="t-1", name="T", node_type=NodeType.TASK, worker_type="w1")],
        edges=[],
    )
    workflow_repo.save(workflow)
    run = WorkflowRun(id="run-1", workflow_id="wf-task", status=RunStatus.RUNNING,
                      context=RunContext())
    run_repo.save(run)

    engine = _mock_engine(workflow_repo)
    resume_run = _build_resume_run(logger, run_repo, task_repo, workflow_repo, stage_recorder, engine)
    resume_run.execute = AsyncMock()  # spy: must NOT be called
    container = {"resume_run": resume_run, "run_repo": run_repo, "workflow_repo": workflow_repo}
    consumer = TimerFiredConsumer(container, broker=MagicMock())

    await consumer._handle(_timer_fired_msg(node_id="t-1"))

    resume_run.execute.assert_not_called()
