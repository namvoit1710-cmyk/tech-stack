"""Regression: ``/test/full`` reset must survive a CAS version drift.

The reuse (reset) branch of ``TestFullWorkflowUseCase`` loads the run OUTSIDE
the engine lock, then acquires the lock and CAS-saves. While it waits for the
lock, the prior cycle's still-in-flight orchestration (an async queue worker)
advances the row's VERSION. A CAS save against the stale version 409s with
``OptimisticConcurrencyError`` — and the whole reset, including the NEW
``input_data``, is dropped, leaving the run on the previous test's values
(the user-visible "keeps the old value" symptom).

The fix re-reads the run INSIDE the lock so the CAS base is current. These
tests pin it with a **non-aliasing** CAS repo (the in-memory repo aliases reads,
which masks CAS races) and an engine whose lock acquisition simulates the
concurrent version bump.
"""
from __future__ import annotations

import copy

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
    TestFullWorkflowCommand, TestFullWorkflowUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger, DummyOrchestrationEngine


class _CASRunRepo:
    """Non-aliasing CAS run repo: reads return deep copies, save is a real
    compare-and-swap on ``version`` (mirrors the HANA repo's semantics so a
    stale read → stale save raises, unlike the aliasing memory repo)."""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def bump_version(self, run_id: str) -> None:
        """Simulate a concurrent writer (the prior cycle's queue worker)
        committing a write while the reset waits for the lock."""
        cur = self._store.get(run_id)
        if cur is not None:
            cur.version += 1

    def get_by_id(self, run_id):  # sync — live ref, for assertions
        return self._store.get(run_id)

    async def aget_by_id(self, run_id):
        cur = self._store.get(run_id)
        return copy.deepcopy(cur) if cur is not None else None

    async def aget_by_id_version(self, run_id, workflow_version):
        return await self.aget_by_id(run_id)

    async def asave(self, run):
        existing = self._store.get(run.id)
        if existing is None:
            run.version = 1
            self._store[run.id] = copy.deepcopy(run)
            return
        if existing.version != run.version:
            raise OptimisticConcurrencyError(
                run.id, expected=run.version, actual=existing.version,
            )
        run.version = existing.version + 1
        self._store[run.id] = copy.deepcopy(run)


class _DriftLock:
    """Async ctx mgr whose acquisition bumps the run's stored version once —
    standing in for the prior cycle's orchestration committing between the
    reset's out-of-lock read and its save."""

    def __init__(self, repo: _CASRunRepo, run_id: str) -> None:
        self._repo, self._run_id = repo, run_id

    async def __aenter__(self):
        self._repo.bump_version(self._run_id)
        return self

    async def __aexit__(self, *exc):
        return False


class _DriftEngine(DummyOrchestrationEngine):
    def __init__(self, repo: _CASRunRepo) -> None:
        super().__init__()
        self._cas_repo = repo

    def run_lock(self, run_id):
        return _DriftLock(self._cas_repo, run_id)

    # The use case's reset path now enters the reentrant ``locked_run``
    # variant; reuse the same drift-injecting context manager.
    def locked_run(self, run_id):
        return _DriftLock(self._cas_repo, run_id)


def _seed_workflow(repo: MemoryWorkflowRepository) -> Workflow:
    wf = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="n1", name="N1", node_type=NodeType.TASK, worker_type="w"),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="n1")],
    )
    repo.save(wf)
    return wf


def _use_case(workflow_repo, run_repo, task_repo, engine):
    return TestFullWorkflowUseCase(
        logger=DummyLogger(), workflow_repo=workflow_repo,
        run_repo=run_repo, task_repo=task_repo,
        orchestration_engine=engine,
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
    )


@pytest.mark.asyncio
async def test_reset_survives_version_drift_and_applies_new_input():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = _CASRunRepo()
    task_repo = MemoryTaskRepository()
    _seed_workflow(workflow_repo)
    engine = _DriftEngine(run_repo)
    uc = _use_case(workflow_repo, run_repo, task_repo, engine)

    # First execution creates the test run (brand-new path, no lock drift).
    first = await uc.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={"x": 1},
    ))
    assert first.error == ""
    run_id = first.run_id

    # Re-execute. The reset acquires the lock (which bumps the stored version,
    # simulating the prior cycle) — without the in-lock re-read the CAS save
    # would now raise OptimisticConcurrencyError and drop the new input.
    second = await uc.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={"x": 2},
    ))

    assert second.run_id == run_id, "reset must reuse the test run id"
    assert second.error == "", f"reset must not surface an error: {second.error!r}"

    stored = run_repo.get_by_id(run_id)
    assert stored.input_data == {"x": 2}, "new input_data must be persisted"
    assert stored.context.variables["input"] == {"x": 2}
    assert stored.generation == 1, "reset must bump generation (on the fresh row)"


@pytest.mark.asyncio
async def test_stale_read_then_save_would_409_without_the_fix():
    """Guard the test's own premise: a CAS save against a version the store has
    moved past raises — i.e. the drift the fix defends against is real."""
    repo = _CASRunRepo()

    class _R:  # minimal run-like with id + version
        id = "r1"
        version = 1

    await repo.asave(_R())                 # insert → version 1
    stale = await repo.aget_by_id("r1")    # read at version 1
    repo.bump_version("r1")                # concurrent writer → store at 2
    with pytest.raises(OptimisticConcurrencyError):
        await repo.asave(stale)            # stale (v1) vs store (v2) → 409
