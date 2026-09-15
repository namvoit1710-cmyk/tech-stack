"""Tests for GetRunContextUseCase."""

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.entities.task import Task
from app.layer1_domain.value_objects import RunStatus, TaskStatus, NodeType
from app.layer2_application.features.get_run_context.use_cases.get_run_context_use_case import (
    GetRunContextUseCase,
    GetRunContextCommand,
    _classify_entry,
    _extract_file_id,
)
from tests.conftest import DummyLogger
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from tests.helpers.view_store_doubles import LiveViewStoreDouble as LiveRunContextViewStore


def _build_uc():
    logger = DummyLogger()
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    wf_repo = MemoryWorkflowRepository()
    view_store = LiveRunContextViewStore(run_repo)
    uc = GetRunContextUseCase(
        logger=logger,
        run_context_view_store=view_store,
        task_repo=task_repo,
        workflow_repo=wf_repo,
    )
    return uc, run_repo, task_repo, wf_repo


def _make_workflow(nodes=None, edges=None):
    return Workflow(
        id="wf-1", name="Test WF",
        nodes=nodes or [
            WorkflowNode(id="n1", name="Extract", node_type=NodeType.TASK),
            WorkflowNode(id="n2", name="Transform", node_type=NodeType.TASK),
        ],
        edges=edges or [],
    )


# ── classify / extract helpers ────────────────────────────────────


class TestClassifyEntry:
    def test_file_ref(self):
        val = {"output": {"uri": "f-abc-123"}, "metadata": {"rows": 100}}
        dt, fid = _classify_entry(val)
        assert dt == "file_ref"
        assert fid == "f-abc-123"

    def test_inline_dict(self):
        val = {"output": {"result": 42}, "metadata": {}}
        dt, fid = _classify_entry(val)
        assert dt == "inline"
        assert fid is None

    def test_inline_no_output(self):
        val = {"customer_id": "C-001"}
        dt, fid = _classify_entry(val)
        assert dt == "inline"
        assert fid is None

    def test_non_dict(self):
        dt, fid = _classify_entry("hello")
        assert dt == "inline"
        assert fid is None


class TestExtractFileId:
    def test_simple_id(self):
        assert _extract_file_id("f-abc-123") == "f-abc-123"

    def test_path_uri(self):
        assert _extract_file_id("files/f-abc-123") == "f-abc-123"

    def test_empty(self):
        assert _extract_file_id("") is None

    def test_none(self):
        assert _extract_file_id(None) is None


# ── use case tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_not_found():
    uc, *_ = _build_uc()
    result = await uc.execute(GetRunContextCommand(run_id="missing"))
    assert result.error == "Run not found"


@pytest.mark.asyncio
async def test_empty_context():
    uc, run_repo, task_repo, wf_repo = _build_uc()
    wf = _make_workflow()
    wf_repo.save(wf)
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {"customer_id": "C-001"}
    run_repo.save(run)

    result = await uc.execute(GetRunContextCommand(run_id="r1"))
    assert result.error is None
    assert result.run_status == "RUNNING"
    assert len(result.entries) == 1
    assert result.entries[0].key == "input"
    assert result.entries[0].data_type == "inline"
    assert result.entries[0].summary["customer_id"] == "C-001"


@pytest.mark.asyncio
async def test_inline_and_file_ref_classification():
    uc, run_repo, task_repo, wf_repo = _build_uc()
    wf = _make_workflow()
    wf_repo.save(wf)

    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    run.context.variables["input"] = {"x": 1}
    run.context.variables["n1"] = {"output": {"uri": "f-file-1"}, "metadata": {"rows": 500}}
    run.context.variables["n2"] = {"output": {"result": 42}, "metadata": {}}
    # Name alias — should be excluded
    run.context.variables["Extract"] = run.context.variables["n1"]
    run_repo.save(run)

    task = Task(id="t1", run_id="r1", node_id="n1", status=TaskStatus.COMPLETED, completed_at="2026-01-01T00:00:00Z")
    task_repo.save(task)

    result = await uc.execute(GetRunContextCommand(run_id="r1"))
    assert result.error is None

    keys = {e.key for e in result.entries}
    assert "input" in keys
    assert "n1" in keys
    assert "n2" in keys
    assert "Extract" not in keys  # alias excluded

    n1_entry = next(e for e in result.entries if e.key == "n1")
    assert n1_entry.data_type == "file_ref"
    assert n1_entry.file_id == "f-file-1"
    assert n1_entry.updated_at == "2026-01-01T00:00:00Z"

    n2_entry = next(e for e in result.entries if e.key == "n2")
    assert n2_entry.data_type == "inline"
    assert n2_entry.file_id is None
