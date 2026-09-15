"""Unit tests for GetRunArtifactsUseCase."""

import pytest

from app.layer1_domain.entities.data_reference import (
    add_artifact, build_file_ref, register_artifacts,
)
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import RunStatus
from app.layer2_application.features.get_run_artifacts.use_cases.get_run_artifacts_use_case import (
    GetRunArtifactsCommand,
    GetRunArtifactsUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from tests.conftest import DummyLogger


def _uc():
    return GetRunArtifactsUseCase(
        logger=DummyLogger(), run_repo=MemoryRunRepository(),
    )


def _run(variables=None):
    return WorkflowRun(
        id="r-1", workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables=variables or {}),
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_returns_registry_populated_by_register_artifacts():
    uc = _uc()
    ref = build_file_ref(file_id="f_main")
    add_artifact(
        ref, file_id="f_invoice", name="invoice_pdf",
        content_type="application/pdf", size_bytes=12345,
    )
    run = _run()
    register_artifacts(
        run.context.variables, ref, node_id="upload_invoice", task_id="t-1",
    )
    uc.run_repo.save(run)

    r = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))

    assert r.error is None
    assert "invoice_pdf" in r.artifacts
    entry = r.artifacts["invoice_pdf"]
    assert entry["file_id"] == "f_invoice"
    assert entry["content_type"] == "application/pdf"
    assert entry["produced_by_node"] == "upload_invoice"
    assert entry["produced_by_task"] == "t-1"
    assert r.etag.startswith("sha256:")


@pytest.mark.asyncio
async def test_empty_registry_returns_empty_etag_sentinel():
    uc = _uc()
    uc.run_repo.save(_run())
    r = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))
    assert r.artifacts == {}
    assert r.etag == "sha256:empty"


@pytest.mark.asyncio
async def test_etag_is_deterministic_for_identical_registry():
    uc = _uc()
    run = _run(variables={
        "artifacts": {
            "a": {"file_id": "f1", "content_type": "text/csv", "size_bytes": 10,
                  "produced_by_node": "n", "produced_by_task": "t", "registered_at": "z"},
        }
    })
    uc.run_repo.save(run)
    r1 = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))
    r2 = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))
    assert r1.etag == r2.etag


@pytest.mark.asyncio
async def test_etag_changes_when_entry_changes():
    uc = _uc()
    run = _run(variables={
        "artifacts": {
            "a": {"file_id": "f1", "content_type": "text/csv", "size_bytes": 10,
                  "produced_by_node": "n", "produced_by_task": "t", "registered_at": "z"},
        }
    })
    uc.run_repo.save(run)
    r1 = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))

    run.context.variables["artifacts"]["a"]["file_id"] = "f2"
    uc.run_repo.save(run)
    r2 = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))

    assert r1.etag != r2.etag


# ---------------------------------------------------------------------------
# Tolerance / legacy data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filters_legacy_bare_string_entries():
    """Pre-Unit-A registries stored bare file_id strings. Those aren't usable
    under the new contract — silently filter them so FE sees only valid entries."""
    uc = _uc()
    run = _run(variables={
        "artifacts": {
            "legacy_string": "f_bare",
            "new_obj": {"file_id": "f2", "content_type": "text/csv",
                        "size_bytes": 0, "produced_by_node": "n",
                        "produced_by_task": "t", "registered_at": "z"},
        }
    })
    uc.run_repo.save(run)
    r = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))
    assert list(r.artifacts.keys()) == ["new_obj"]


@pytest.mark.asyncio
async def test_filters_entries_missing_file_id():
    uc = _uc()
    run = _run(variables={
        "artifacts": {
            "incomplete": {"filename": "x.pdf"},
            "good": {"file_id": "f1", "content_type": "text/csv", "size_bytes": 0,
                     "produced_by_node": "n", "produced_by_task": "t", "registered_at": "z"},
        }
    })
    uc.run_repo.save(run)
    r = await uc.execute(GetRunArtifactsCommand(run_id="r-1"))
    assert list(r.artifacts.keys()) == ["good"]


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_not_found():
    uc = _uc()
    r = await uc.execute(GetRunArtifactsCommand(run_id="missing"))
    assert r.error_code == "RUN_NOT_FOUND"
    assert r.error_details == {"run_id": "missing"}
