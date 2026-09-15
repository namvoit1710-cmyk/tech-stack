"""The ``output_metadata`` contract is WIRED into the completion path.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §18.5.

``test_task_metadata_contract.py`` proves the contract is right. This proves it
is *connected* — a distinction this workstream has already paid for once: the
`DROP CONTEXT` topology fix had nine green tests while the call that applied it
was deleted, because every test invoked the helper directly. **An unwired fix is
indistinguishable from no fix.**

So these drive the real ``CompleteTaskUseCase.execute`` and read the result back
out of the task repository, which is the only thing production cares about.

Two properties matter equally, and the second is the one that would be a data
loss if it were wrong:

1. what the TASK stores is narrowed;
2. what the NODE outputs is **not** — ``store_data`` must still receive the whole
   payload, because that is the copy downstream nodes and the Output panel read.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.data import task_metadata
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
    CompleteTaskUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import (
    MemoryTaskRepository,
)
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from tests.conftest import DummyLogger, DummyOrchestrationEngine


class _RecordingDataStore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def store_data(self, node_id: str, data):
        self.calls.append((node_id, data))
        return build_file_ref(file_id="f1", row_count=1, columns=["a"])


class _CapturingLogger(DummyLogger):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def info(self, msg, *args, **kwargs):  # noqa: D102
        try:
            self.lines.append(msg % args if args else str(msg))
        except Exception:  # noqa: BLE001
            self.lines.append(str(msg))


def _seed(workflow_repo, run_repo, task_repo, worker_type: str) -> None:
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="gen", name="gen", node_type=NodeType.TASK,
                         worker_type=worker_type),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="gen")],
    )
    workflow_repo.save(workflow)
    run_repo.save(WorkflowRun(
        id="r1", workflow_id="wf", status=RunStatus.RUNNING,
        context=RunContext(
            topology=GraphTopology(
                adjacency={"t": ["gen"], "gen": []},
                predecessors={"t": [], "gen": ["t"]},
                edge_map={"t": workflow.edges, "gen": []},
                entry_nodes=["t"], leaf_nodes=["gen"],
            ),
            completed_nodes={"t"}, variables={"t": {"output": {}}},
        ),
    ))
    task_repo.save(Task(id="r1-gen", run_id="r1", node_id="gen",
                        status=TaskStatus.RUNNING, worker_type=worker_type))


async def _complete(output: dict, *, worker_type: str = "http_request"):
    """Run the real use case; return (saved task, data-store calls, logger)."""
    workflow_repo = MemoryWorkflowRepository()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    _seed(workflow_repo, run_repo, task_repo, worker_type)
    store = _RecordingDataStore()
    logger = _CapturingLogger()

    uc = CompleteTaskUseCase(
        logger=logger, workflow_repo=workflow_repo, run_repo=run_repo,
        task_repo=task_repo, orchestration_engine=DummyOrchestrationEngine(),
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
        data_store_service=store,
    )
    await uc.execute(CompleteTaskCommand(
        run_id="r1", task_id="r1-gen", output_reference="", output_metadata=output,
    ))
    return task_repo.get_by_id("r1-gen"), store, logger


def _worker_payload() -> dict:
    """The measured shape of the heaviest real row: a ``get_comments``
    completion, 642,530 chars of which four were read by the control plane."""
    return {
        "raw": {"body": "x" * 400},
        "comments": [{"id": i, "text": "y" * 40} for i in range(20)],
        "issue_key": "SA-1",
        "success": True,
        "count": 20,
        "error_code": "",
    }


@pytest.mark.asyncio
class TestOffChangesNothing:
    """It ships inert. The default must be byte-for-byte today's behaviour."""

    async def test_the_default_mode_is_off(self):
        assert task_metadata.TASK_METADATA_MODE == "off"

    async def test_metadata_is_stored_whole(self):
        payload = _worker_payload()
        task, _store, _log = await _complete(payload)
        assert task.output_metadata == payload


@pytest.mark.asyncio
class TestOnNarrowsTheTask:
    async def test_only_the_contract_is_persisted(self, monkeypatch):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")
        task, _store, _log = await _complete(_worker_payload())
        assert set(task.output_metadata) == {"error_code"}, (
            "the task must keep the control-plane contract and nothing else"
        )

    async def test_a_terminal_key_from_the_worker_survives(self, monkeypatch):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")
        task, _store, _log = await _complete(
            {"error_code": "WORKER_TIMEOUT", "raw": {"b": "x" * 500}})
        assert task.output_metadata == {"error_code": "WORKER_TIMEOUT"}

    async def test_it_is_logged(self, monkeypatch):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")
        _task, _store, log = await _complete(_worker_payload())
        assert any("task-metadata narrowed" in line for line in log.lines)


@pytest.mark.asyncio
class TestTheNODEOUTPUTIsNeverTouched:
    """The property that makes this a deletion rather than a data loss: the
    payload's real homes are fed from ``task_output``, not from the task row.
    §17.8 verified per task that 96.3% of it is in AIW_RUN_NODE_OUTPUT and the
    rest in AIW_EVENT_LOG — but only while this keeps receiving it."""

    async def test_store_data_still_gets_the_whole_payload(self, monkeypatch):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")
        task, store, _log = await _complete(_worker_payload())

        assert store.calls, "the node output must still be stored"
        _node_id, stored = store.calls[0]
        assert isinstance(stored, dict)
        for key in ("raw", "comments", "issue_key", "count"):
            assert key in stored, (
                f"{key} was dropped from the NODE OUTPUT — narrowing must only "
                f"affect what the TASK stores"
            )
        # and the task really was narrowed in the same run
        assert set(task.output_metadata) == {"error_code"}

    async def test_the_metadata_envelope_survives_in_the_output(self, monkeypatch):
        """``task_output['metadata']`` is what ``{{$node.output.metadata}}``
        resolves against; narrowing the task must not empty it."""
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")
        _task, store, _log = await _complete(_worker_payload())
        _node_id, stored = store.calls[0]
        assert stored.get("metadata"), "the metadata envelope must not be emptied"


@pytest.mark.asyncio
class TestShadowMeasuresWithoutChanging:
    async def test_the_task_keeps_everything(self, monkeypatch):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "shadow")
        payload = _worker_payload()
        task, _store, _log = await _complete(payload)
        assert task.output_metadata == payload

    async def test_but_it_reports_what_it_would_drop(self, monkeypatch):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "shadow")
        _task, _store, log = await _complete(_worker_payload())
        line = next((x for x in log.lines if "task-metadata shadow" in x), "")
        assert line, "shadow mode must report, or it buys nothing"
        assert "comments" in line and "raw" in line


@pytest.mark.asyncio
class TestItCannotBreakACompletion:
    """The authoritative output is already stored by the time this runs, so no
    metadata shape may fail the completion."""

    @pytest.mark.parametrize("weird", [{}, {"only": "payload"}])
    async def test_odd_metadata_still_completes(self, monkeypatch, weird):
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")
        task, _store, _log = await _complete(weird)
        assert task.status == TaskStatus.COMPLETED

    async def test_a_raising_narrow_falls_back_to_the_original(self, monkeypatch):
        """Belt-and-braces: if the contract module itself throws, completion
        keeps today's value rather than losing it."""
        monkeypatch.setattr(task_metadata, "TASK_METADATA_MODE", "on")

        def _boom(_metadata):
            raise RuntimeError("contract exploded")

        monkeypatch.setattr(task_metadata, "narrow", _boom)
        payload = _worker_payload()
        task, _store, _log = await _complete(payload)
        assert task.status == TaskStatus.COMPLETED
        assert task.output_metadata == payload
