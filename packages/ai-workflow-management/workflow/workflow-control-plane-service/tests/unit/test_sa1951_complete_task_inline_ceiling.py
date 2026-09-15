"""SA-1951 — behavioural cover for complete_task's inline/file-back decision.

The predicate is unit-tested in ``test_sa1951_inline_size_ceiling``; this asserts
the thing that actually bit us: whether ``store_data`` is SKIPPED. A ``code``
node's output used to skip it unconditionally, so 60k rows stayed inline and then
413'd on the way to the broker — the result vanished and the node hung forever.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand, CompleteTaskUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger, DummyOrchestrationEngine


class _RecordingDataStore:
    """Records whether the output was file-backed, and with what."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def store_data(self, node_id: str, data):
        self.calls.append((node_id, data))
        return build_file_ref(file_id="file_stored", row_count=60_000, columns=["sku", "qty"])


def _seed(workflow_repo, run_repo, task_repo, *, worker_type: str):
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="gen", name="gen", node_type=NodeType.TASK, worker_type=worker_type),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="gen")],
    )
    workflow_repo.save(workflow)

    topology = GraphTopology(
        adjacency={"t": ["gen"], "gen": []},
        predecessors={"t": [], "gen": ["t"]},
        edge_map={"t": workflow.edges, "gen": []},
        entry_nodes=["t"],
        leaf_nodes=["gen"],
    )
    run_repo.save(WorkflowRun(
        id="r1", workflow_id="wf", status=RunStatus.RUNNING,
        context=RunContext(topology=topology, completed_nodes={"t"}, variables={"t": {"output": {}}}),
    ))
    task_repo.save(Task(
        id="r1-gen", run_id="r1", node_id="gen",
        status=TaskStatus.RUNNING, worker_type=worker_type,
    ))


async def _complete(worker_type: str, output: dict) -> _RecordingDataStore:
    workflow_repo, run_repo, task_repo = (
        MemoryWorkflowRepository(), MemoryRunRepository(), MemoryTaskRepository(),
    )
    _seed(workflow_repo, run_repo, task_repo, worker_type=worker_type)
    store = _RecordingDataStore()

    uc = CompleteTaskUseCase(
        logger=DummyLogger(), workflow_repo=workflow_repo, run_repo=run_repo,
        task_repo=task_repo, orchestration_engine=DummyOrchestrationEngine(),
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
        data_store_service=store,
    )
    await uc.execute(CompleteTaskCommand(
        run_id="r1", task_id="r1-gen", output_reference="", output_metadata=output,
    ))
    return store


def _small():
    return {"result": [{"sku": "ABC-001", "qty": 12}]}


def _large():
    return {"result": [{"sku": f"SKU-{i:06d}", "qty": i} for i in range(60_000)]}


@pytest.mark.asyncio
async def test_small_code_output_stays_inline():
    """The original contract must survive: small code output round-trips verbatim."""
    store = await _complete("code", _small())
    assert store.calls == [], "a small code output must NOT be file-backed"


@pytest.mark.asyncio
async def test_large_code_output_is_file_backed():
    """The fix: an oversized 'inline' output is stored like any other worker's."""
    store = await _complete("code", _large())
    assert len(store.calls) == 1, "an oversized code output MUST be file-backed"
    node_id, data = store.calls[0]
    assert node_id == "gen"
    assert data["result"][0]["sku"] == "SKU-000000"


@pytest.mark.asyncio
async def test_large_approval_flow_output_is_file_backed():
    store = await _complete("approval_flow", _large())
    assert len(store.calls) == 1


@pytest.mark.asyncio
async def test_non_inline_worker_is_file_backed_as_before():
    store = await _complete("http_request", _small())
    assert len(store.calls) == 1, "unchanged: non-inline workers are always file-backed"
