"""Regression: ``test_run_id`` must survive a version-creating workflow save.

Saving a workflow with changes mints a new ``workflow.version`` uuid while the
new version row carries over the old ``test_run_id``. ``/test/full`` used to
look the run up with ``aget_by_id_version(test_run_id, workflow.version)`` —
the run row keeps the version it was created under, so the lookup missed after
every save and a NEW run id was minted. The frontend subscribes to realtime
events keyed by the run_id it read before the save, and ``orchestrate_start``
fires before the HTTP response returns, so the rotated run's early events were
never captured.

The fix looks the run up by id only and re-stamps ``run.workflow_version``
inside the reuse branch (mirroring ``reset_test_run``). These tests pin both
with a run repo whose version-scoped lookup is HANA-faithful
(``WHERE ID = ? AND WORKFLOW_VERSION = ?``) — under the old code the second
``/test/full`` after a save would mint a fresh run id and go red here.
"""
from __future__ import annotations

import contextlib
import uuid

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import (
    TestFullWorkflowCommand, TestFullWorkflowUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_snapshot_repo import (
    MemoryWorkflowSnapshotRepository,
)
from tests.conftest import DummyLogger, DummyOrchestrationEngine


class _VersionScopedRunRepo(MemoryRunRepository):
    """HANA-faithful version-scoped lookup: a run is only found when its
    stored ``workflow_version`` matches — the plain memory repo inherits a
    Protocol stub that silently returns None instead."""

    async def aget_by_id_version(self, id, workflow_version):
        run = self.get_by_id(id)
        if run is None or run.workflow_version != workflow_version:
            return None
        return run


class _LockableEngine(DummyOrchestrationEngine):
    def locked_run(self, run_id):
        return contextlib.nullcontext()


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


def _save_as_new_version(workflow_repo: MemoryWorkflowRepository) -> str:
    """Mimic ``UpdateWorkflowUseCase``'s new-version branch: fresh version
    uuid on the latest row, ``test_run_id`` carried over untouched."""
    wf = workflow_repo.get_by_id("wf")
    wf.version = str(uuid.uuid4())
    wf.workflow_id = str(uuid.uuid4())
    workflow_repo.save(wf)
    return wf.version


def _use_case(workflow_repo, run_repo, task_repo):
    return TestFullWorkflowUseCase(
        logger=DummyLogger(),
        workflow_repo=workflow_repo,
        workflow_snapshot_repo=MemoryWorkflowSnapshotRepository(),
        run_repo=run_repo,
        task_repo=task_repo,
        orchestration_engine=_LockableEngine(),
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
    )


@pytest.mark.asyncio
async def test_run_id_stable_across_version_creating_save():
    workflow_repo = MemoryWorkflowRepository()
    run_repo = _VersionScopedRunRepo()
    task_repo = MemoryTaskRepository()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, task_repo)

    first = await uc.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={"x": 1},
    ))
    assert first.error == ""

    new_version = _save_as_new_version(workflow_repo)

    second = await uc.execute(TestFullWorkflowCommand(
        workflow_id="wf", input_data={"x": 2},
    ))
    assert second.error == ""
    assert second.run_id == first.run_id, (
        "test_run_id must not rotate after a version-creating save — the "
        "frontend's realtime subscription is keyed by the pre-save run_id"
    )
    assert workflow_repo.get_by_id("wf").test_run_id == first.run_id

    stored = run_repo.get_by_id(first.run_id)
    assert stored.workflow_version == new_version, (
        "reuse branch must re-stamp run.workflow_version to the version "
        "it actually executes"
    )
    assert stored.input_data == {"x": 2}
    assert stored.generation == 1, "reuse branch must bump generation"


@pytest.mark.asyncio
async def test_run_reused_in_mock_mode_without_version_lookup():
    """Plain memory repo: ``aget_by_id_version`` is an inherited Protocol
    stub returning None, so the old version-scoped lookup minted a new run
    on EVERY /test/full. The by-id lookup must reuse it."""
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed_workflow(workflow_repo)
    uc = _use_case(workflow_repo, run_repo, task_repo)

    first = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    second = await uc.execute(TestFullWorkflowCommand(workflow_id="wf"))
    assert first.error == "" and second.error == ""
    assert second.run_id == first.run_id
