"""Guard integration in CompleteTaskUseCase and FailTaskUseCase.

Reject a callback whose ``task.run_generation`` is less than the
run's current ``generation`` — the task belongs to a superseded
execution (typically: test-run was reset and re-executed between
dispatch and callback).

See ``docs/design/run-generation-guard.md``.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand, CompleteTaskUseCase,
)
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import (
    FailTaskCommand, FailTaskUseCase,
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
    """Pin ``GENERATION_GUARD_MODE=on`` for these tests so the default
    behaviour is exercised. Restores the prior value afterwards."""
    from app.layer4_frameworks.config import app_config
    original = app_config.settings.GENERATION_GUARD_MODE
    app_config.settings.GENERATION_GUARD_MODE = "on"
    yield
    app_config.settings.GENERATION_GUARD_MODE = original


def _seed(
    workflow_repo: MemoryWorkflowRepository,
    run_repo: MemoryRunRepository,
    task_repo: MemoryTaskRepository,
    *,
    run_generation: int,
    task_generation: int,
    task_status: TaskStatus = TaskStatus.RUNNING,
) -> None:
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n1", name="N1", node_type=NodeType.TASK, worker_type="w"),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="n1")],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology(
        adjacency={"t": ["n1"], "n1": []},
        predecessors={"t": [], "n1": ["t"]},
        edge_map={"t": workflow.edges, "n1": []},
        entry_nodes=["t"],
        leaf_nodes=["n1"],
    )
    run = WorkflowRun(
        id="r1", workflow_id="wf", status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology,
            completed_nodes={"t"},
            variables={"t": {"output": {}}},
        ),
        generation=run_generation,
    )
    run_repo.save(run)

    task = Task(
        id="r1-n1", run_id="r1", node_id="n1",
        status=task_status, worker_type="w",
        run_generation=task_generation,
    )
    task_repo.save(task)


def _build(engine, logger, workflow_repo, run_repo, task_repo):
    stage_rec = StageRecorder(repository=MemoryStageHistoryRepository())
    complete = CompleteTaskUseCase(
        logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
        task_repo=task_repo, orchestration_engine=engine,
        stage_recorder=stage_rec,
    )
    fail = FailTaskUseCase(
        logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
        task_repo=task_repo, orchestration_engine=engine,
        stage_recorder=stage_rec,
    )
    return complete, fail


@pytest.mark.asyncio
async def test_complete_task_rejects_stale_generation():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed(workflow_repo, run_repo, task_repo, run_generation=2, task_generation=1)

    engine = DummyOrchestrationEngine()
    complete, _ = _build(engine, DummyLogger(), workflow_repo, run_repo, task_repo)

    result = await complete.execute(CompleteTaskCommand(
        run_id="r1", task_id="r1-n1",
        output_reference="s3://out", output_metadata={},
    ))

    assert result.completed is False
    assert result.error_code == ErrorCode.TASK_SUPERSEDED.code
    assert result.error_details["task_generation"] == 1
    assert result.error_details["run_generation"] == 2

    # Critical: the stale task must NOT have been mutated to COMPLETED.
    persisted = task_repo.get_by_id("r1-n1")
    assert persisted.status == TaskStatus.RUNNING


@pytest.mark.asyncio
async def test_complete_task_passes_matching_generation():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed(workflow_repo, run_repo, task_repo, run_generation=3, task_generation=3)

    engine = DummyOrchestrationEngine()
    complete, _ = _build(engine, DummyLogger(), workflow_repo, run_repo, task_repo)

    result = await complete.execute(CompleteTaskCommand(
        run_id="r1", task_id="r1-n1",
        output_reference="s3://out", output_metadata={},
    ))

    assert result.completed is True
    assert task_repo.get_by_id("r1-n1").status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_fail_task_rejects_stale_generation():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed(workflow_repo, run_repo, task_repo, run_generation=2, task_generation=0)

    engine = DummyOrchestrationEngine()
    _, fail = _build(engine, DummyLogger(), workflow_repo, run_repo, task_repo)

    result = await fail.execute(FailTaskCommand(
        run_id="r1", task_id="r1-n1",
        error={"message": "boom"},
    ))

    assert result.failed is False
    assert result.error_code == ErrorCode.TASK_SUPERSEDED.code

    # Stale task stays RUNNING; no transition to FAILED.
    persisted = task_repo.get_by_id("r1-n1")
    assert persisted.status == TaskStatus.RUNNING


# ── Option 1: re-drive on replay (no terminal-status short-circuit) ──
#
# A redelivered task.completed/failed re-runs the body without raising. Run-state
# idempotency is provided by the SINKS — deterministic ``NodeOutputProduced``
# dedupe (event-log unique (run_id, event_id), append returns False on collision)
# and the projector's ``completed_nodes`` frozenset fold (see
# test_run_state_projector) — not by skipping the work. These unit tests assert
# the use case re-drives (re-emits) instead of short-circuiting.


@pytest.mark.asyncio
async def test_fail_redrives_on_duplicate():
    """A redelivered ``task.failed`` for an already-FAILED task re-runs the body
    (no short-circuit) without raising, and re-emits NodeFailed."""
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed(workflow_repo, run_repo, task_repo, run_generation=1, task_generation=1,
          task_status=TaskStatus.FAILED)

    engine = DummyOrchestrationEngine()
    emitted: list = []
    engine.emit_node_failed = lambda *a, **k: emitted.append((a, k))
    _, fail = _build(engine, DummyLogger(), workflow_repo, run_repo, task_repo)

    result = await fail.execute(FailTaskCommand(
        run_id="r1", task_id="r1-n1", error={"message": "second delivery"}))

    assert result.failed is True            # re-drove, no raise
    assert len(emitted) == 1                # NodeFailed re-emitted (not short-circuited)
    assert task_repo.get_by_id("r1-n1").status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_complete_redrives_on_duplicate():
    """A redelivered ``task.completed`` for an already-COMPLETED task re-runs the
    body without raising, and re-emits NodeCompleted."""
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed(workflow_repo, run_repo, task_repo, run_generation=1, task_generation=1,
          task_status=TaskStatus.COMPLETED)

    engine = DummyOrchestrationEngine()
    emitted: list = []
    engine.emit_node_completed = lambda *a, **k: emitted.append((a, k))
    complete, _ = _build(engine, DummyLogger(), workflow_repo, run_repo, task_repo)

    result = await complete.execute(CompleteTaskCommand(
        run_id="r1", task_id="r1-n1", output_reference="s3://out", output_metadata={"x": 1}))

    assert result.completed is True         # re-drove, no raise
    assert len(emitted) == 1                # NodeCompleted re-emitted (not short-circuited)
    assert task_repo.get_by_id("r1-n1").status == TaskStatus.COMPLETED
    # The frozenset idempotency that makes this re-drive safe at the run-state
    # level is covered by test_run_state_projector
    # ("Replaying the same NodeCompleted must not double-count").
