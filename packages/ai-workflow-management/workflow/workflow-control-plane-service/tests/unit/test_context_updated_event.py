"""Tests for ContextUpdated event emission from complete_task."""

import pytest

pytestmark = pytest.mark.skip(reason="legacy inline-execution coverage — rewrite to use tests.helpers.queue_runner for queued-mode regression coverage")

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.entities.task import Task
from app.layer1_domain.events import EventTopic, NodeCompleted, ContextUpdated
from app.layer1_domain.value_objects import RunStatus, TaskStatus, NodeType
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskUseCase, CompleteTaskCommand,
)
from tests.conftest import DummyLogger, DummyStageRecorder, DummyEventPublisher
from app.layer2_application.interfaces.orchestration_engine_interface import IOrchestrationEngine
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository


class _SpyEngine(IOrchestrationEngine):
    """Captures publish_event / emit_* calls."""

    def __init__(self):
        self.events: list[tuple[str, object]] = []

    def publish_event(self, topic, event):
        self.events.append((str(topic), event))

    def publish_events(self, events):
        for ev in events:
            self.events.append((type(ev).__name__.lower(), ev))

    def emit_node_completed(self, task, run, node=None):
        self.events.append((str(EventTopic.NODE_COMPLETED), NodeCompleted(
            task_id=task.id, run_id=task.run_id,
            workflow_id=run.workflow_id, node_id=task.node_id,
            root_run_id=run.root_run_id or run.id, depth=run.depth_level,
            source="workflow-control-plane",
        )))
        self.events.append((str(EventTopic.CONTEXT_UPDATED), ContextUpdated(
            run_id=task.run_id, workflow_id=run.workflow_id,
            node_id=task.node_id,
            node_name=node.name if node else "",
            data_type="file_ref" if task.output_ref else "inline",
            root_run_id=run.root_run_id or run.id, depth=run.depth_level,
            source="workflow-control-plane",
        )))

    async def process_successors(self, *args, **kwargs):
        from app.layer2_application.orchestration.successor_processor import ProcessingResult
        return ProcessingResult()

    async def process_and_dispatch_successors(self, *args, **kwargs):
        from app.layer2_application.orchestration.successor_processor import ProcessingResult
        return ProcessingResult()

    async def check_run_completion(self, *args, **kwargs):
        pass

    async def drain_events(self):
        pass

    async def drain_events(self):
        pass

    async def maybe_enqueue_loop_iteration(self, *args, **kwargs):
        pass


def _build(engine=None):
    logger = DummyLogger()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    wf_repo = MemoryWorkflowRepository()
    stage_recorder = DummyStageRecorder()
    spy = engine or _SpyEngine()

    uc = CompleteTaskUseCase(
        logger=logger, run_repo=run_repo, task_repo=task_repo,
        workflow_repo=wf_repo, stage_recorder=stage_recorder,
        orchestration_engine=spy,
    )
    return uc, run_repo, task_repo, wf_repo, spy


@pytest.mark.asyncio
async def test_context_updated_emitted_on_complete():
    uc, run_repo, task_repo, wf_repo, spy = _build()

    wf = Workflow(id="wf-1", name="W", nodes=[
        WorkflowNode(id="n1", name="Extract", node_type=NodeType.TASK, worker_type="w1"),
    ])
    wf_repo.save(wf)
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="t1", run_id="r1", node_id="n1", status=TaskStatus.RUNNING, worker_type="w1")
    task_repo.save(task)

    await uc.execute(CompleteTaskCommand(
        run_id="r1", task_id="t1",
        output_reference="f-file-123",
        output_metadata={"rows": 500},
    ))

    ctx_events = [(t, e) for t, e in spy.events if t == "context.updated"]
    assert len(ctx_events) == 1
    topic, event = ctx_events[0]
    assert isinstance(event, ContextUpdated)
    assert event.run_id == "r1"
    assert event.node_id == "n1"
    assert event.node_name == "Extract"
    assert event.data_type == "file_ref"


@pytest.mark.asyncio
async def test_context_updated_inline_when_no_output_ref():
    uc, run_repo, task_repo, wf_repo, spy = _build()

    wf = Workflow(id="wf-1", name="W", nodes=[
        WorkflowNode(id="n1", name="Compute", node_type=NodeType.TASK, worker_type="w1"),
    ])
    wf_repo.save(wf)
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run_repo.save(run)
    task = Task(id="t1", run_id="r1", node_id="n1", status=TaskStatus.RUNNING, worker_type="w1")
    task_repo.save(task)

    await uc.execute(CompleteTaskCommand(
        run_id="r1", task_id="t1",
        output_reference="",
        output_metadata={"result": 42},
    ))

    ctx_events = [(t, e) for t, e in spy.events if t == "context.updated"]
    assert len(ctx_events) == 1
    assert ctx_events[0][1].data_type == "inline"
