"""End-to-end re-execute race — ``TestFullWorkflowUseCase`` bumps
``run.generation`` so callbacks from the superseded execution are
rejected by ``CompleteTaskUseCase``.

See ``docs/design/run-generation-guard.md`` §10.2.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand, CompleteTaskUseCase,
)
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
    TestFullWorkflowCommand, TestFullWorkflowUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger, DummyOrchestrationEngine


@pytest.fixture(autouse=True)
def _guard_on():
    from app.layer4_frameworks.config import app_config
    original = app_config.settings.GENERATION_GUARD_MODE
    app_config.settings.GENERATION_GUARD_MODE = "on"
    yield
    app_config.settings.GENERATION_GUARD_MODE = original


def _seed_workflow(workflow_repo: MemoryWorkflowRepository) -> Workflow:
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n1", name="N1", node_type=NodeType.TASK, worker_type="w"),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="n1")],
    )
    workflow_repo.save(workflow)
    return workflow


@pytest.mark.asyncio
async def test_reexecute_bumps_generation_and_rejects_stale_callback():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()

    workflow = _seed_workflow(workflow_repo)

    engine = DummyOrchestrationEngine()
    stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())

    test_full = TestFullWorkflowUseCase(
        logger=DummyLogger(), workflow_repo=workflow_repo,
        run_repo=run_repo, task_repo=task_repo,
        orchestration_engine=engine, stage_recorder=stage_rec,
    )

    # ── First execution: creates the test run at generation 0.
    first = await test_full.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={"x": 1},
    ))
    run_id = first.run_id
    run_after_first = run_repo.get_by_id(run_id)
    assert run_after_first.generation == 0

    # Simulate the dispatcher creating a task for node n1 during this
    # first execution — TaskFactory stamps the current generation.
    first_task = TaskFactory.create_task(
        workflow.get_node("n1"), run_after_first,
    )
    first_task.status = TaskStatus.RUNNING
    task_repo.save(first_task)
    assert first_task.run_generation == 0

    # ── User re-clicks Execute mid-flight.
    second = await test_full.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={"x": 2},
    ))
    assert second.run_id == run_id, "re-execute must reuse the test run id"

    run_after_second = run_repo.get_by_id(run_id)
    assert run_after_second.generation == 1, "reset must bump generation"

    stale = task_repo.get_by_id(first_task.id)
    assert stale.status == TaskStatus.CANCELLED, "stale task must be cancelled by reset"

    # ── The worker that was running ``first_task`` finally finishes
    # and calls back to complete the task. The callback references
    # the OLD task_id — control-plane must reject it.
    complete_uc = CompleteTaskUseCase(
        logger=DummyLogger(), workflow_repo=workflow_repo,
        run_repo=run_repo, task_repo=task_repo,
        orchestration_engine=engine, stage_recorder=stage_rec,
    )
    result = await complete_uc.execute(CompleteTaskCommand(
        run_id=run_id, task_id=first_task.id,
        output_reference="s3://late", output_metadata={"from": "stale-worker"},
    ))

    assert result.completed is False
    assert result.error_code == ErrorCode.TASK_SUPERSEDED.code
    assert result.error_details["task_generation"] == 0
    assert result.error_details["run_generation"] == 1

    # Stale task's final state must remain CANCELLED — the callback
    # must NOT have flipped it to COMPLETED.
    final = task_repo.get_by_id(first_task.id)
    assert final.status == TaskStatus.CANCELLED
    assert final.output_ref in (None, ""), "stale output_ref must not have been written"


@pytest.mark.asyncio
async def test_first_execution_callbacks_succeed():
    """Sanity: fresh run at generation 0 + task stamped 0 matches →
    callback is accepted. The guard is only a no-op for the common
    case; this test pins it."""
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow = _seed_workflow(workflow_repo)

    engine = DummyOrchestrationEngine()
    stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())

    test_full = TestFullWorkflowUseCase(
        logger=DummyLogger(), workflow_repo=workflow_repo,
        run_repo=run_repo, task_repo=task_repo,
        orchestration_engine=engine, stage_recorder=stage_rec,
    )
    result = await test_full.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={},
    ))
    run = run_repo.get_by_id(result.run_id)

    task = TaskFactory.create_task(workflow.get_node("n1"), run)
    task.status = TaskStatus.RUNNING
    task_repo.save(task)

    complete_uc = CompleteTaskUseCase(
        logger=DummyLogger(), workflow_repo=workflow_repo,
        run_repo=run_repo, task_repo=task_repo,
        orchestration_engine=engine, stage_recorder=stage_rec,
    )
    final = await complete_uc.execute(CompleteTaskCommand(
        run_id=run.id, task_id=task.id,
        output_reference="s3://first", output_metadata={},
    ))
    assert final.completed is True
    assert task_repo.get_by_id(task.id).status == TaskStatus.COMPLETED
