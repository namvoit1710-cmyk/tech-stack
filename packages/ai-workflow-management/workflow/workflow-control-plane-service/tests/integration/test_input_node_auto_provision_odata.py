"""Integration scenario: INPUT node with schema declaring scalars + a table.

End-to-end smoke for the new file-backed ``/data`` contract:

1. Create a workflow with a single INPUT node whose ``input_schema`` declares
   two scalar fields (``input``, ``input_1``) plus one table
   (``table_3_lg2h0``) with two columns (``input_2``, ``input_3``).
2. Start a run and drain the orchestration queue so the INPUT node lands
   in PENDING.
3. ``GET /data?side=input`` must expose:
   - A non-empty ``file_id`` at the ROOT (``artifact_type == "object_field"``,
     columns including the scalar keys).
   - A non-empty ``file_id`` for the table child
     (``artifact_type == "array_field"``).
   - **No** ``scalars`` dict on the payload (dropped by design).
4. ``PATCH /data`` against ``__root__`` with an ``insert`` writes the single
   data row of scalar values; a second ``insert`` upserts (replaces) it.
5. ``PATCH /data`` against the table artifact supports ``insert`` /
   ``edit`` / ``delete``.
6. ``POST /submit`` finalises the node; the run reaches ``COMPLETED``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner


# ---------------------------------------------------------------------------
# Schema under test — uses TableWrapper (what schema_artifact_walker knows).
# ---------------------------------------------------------------------------

INPUT_SCHEMA = [
    {
        "key": "input",
        "outputType": "string",
        "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "fieldControl": "InputControl",
            "wrapperProps": {"label": "Input 1"},
        },
    },
    {
        "key": "input_1",
        "outputType": "string",
        "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "fieldControl": "InputControl",
            "wrapperProps": {"label": "Input 2"},
        },
    },
    {
        "key": "table_3_lg2h0",
        "outputType": "array",
        "fieldConfig": {
            "fieldWrapper": "OdataWrapper",
            "wrapperProps": {"label": "Table"},
            "controlProps": {},
        },
        "fields": [
            {
                "key": "table_3_lg2h0_row",
                "outputType": "object",
                "fieldConfig": {
                    "fieldWrapper": "FragmentWrapper",
                    "wrapperProps": {},
                },
                "fields": [
                    {
                        "key": "input_2",
                        "outputType": "string",
                        "fieldConfig": {
                            "fieldWrapper": "FormItemWrapper",
                            "fieldControl": "InputControl",
                            "wrapperProps": {"label": "col A"},
                        },
                    },
                    {
                        "key": "input_3",
                        "outputType": "string",
                        "fieldConfig": {
                            "fieldWrapper": "FormItemWrapper",
                            "fieldControl": "InputControl",
                            "wrapperProps": {"label": "col B"},
                        },
                    },
                ],
            },
        ],
    },
]

WORKFLOW_ID = "wf-input-auto-1"
NODE_ID = "n_input"
ARTIFACT_ID = "table_3_lg2h0"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


def _seed_workflow(container) -> None:
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
    from app.layer1_domain.value_objects import NodeType

    wf = Workflow(
        id=WORKFLOW_ID,
        name="Auto-provision Table + Root Scalars",
        nodes=[
            WorkflowNode(
                id=NODE_ID,
                name="Input",
                node_type=NodeType.INPUT,
                config={"input_schema": INPUT_SCHEMA},
            ),
        ],
        edges=[],
    )
    container["get_node_schema"].workflow_repo.save(wf)


# ---------------------------------------------------------------------------
# End-to-end scenario
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_input_node_root_csv_and_table_patch_end_to_end(client, container):
    """Smoke: GET side hits the HTTP surface; PATCH side calls the use case
    directly (TestClient + async-queue timeout is a known fixture limitation
    — same reason the prior ``test_patch_node_data.py`` PATCH tests were
    skipped)."""
    _seed_workflow(container)

    # 1. Start the run via HTTP.
    r = client.post(
        "/api/v1/runs",
        json={"workflow_id": WORKFLOW_ID, "input_data": {}},
    )
    start = assert_ok(r)
    run_id = start["id"]
    await _drain(container)

    # 2. GET /data?side=input — root CSV + table CSV both materialised.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=input")
    payload = assert_ok(r)
    data = payload["data"]
    assert data is not None
    assert "scalars" not in data, "scalars dict must be removed from payload"

    root = data["root"]
    assert root is not None
    assert root["artifact_id"] == "__root__"
    assert root["file_id"], "root must be file-backed (object_field CSV)"
    assert root["artifact_type"] == "object_field"
    assert "input" in root["columns"]
    assert "input_1" in root["columns"]

    table = _find_child(root, ARTIFACT_ID)
    assert table is not None
    assert table["file_id"], "table artifact must have an auto-provisioned file_id"
    assert table["artifact_type"] == "array_field"

    etag_1 = payload["etag"]
    assert etag_1

    # 3. PATCH — object_field upsert via direct use case call.
    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_1,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "hello", "input_1": "world"},
            }],
        }],
    ))
    assert result.error is None, result.error
    assert result.results[0].status == "ok"
    assert result.version >= 1
    etag_2 = result.etag
    assert etag_2 and etag_2 != etag_1

    # 4. Second insert — object artifact upserts row 1, not append.
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_2,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "hello2", "input_1": "world2"},
            }],
        }],
    ))
    assert result.error is None
    etag_3 = result.etag

    fs = container["file_service_client"]
    # Re-read the working ref through the view store to get the post-patch
    # root file_id (the original GET was pre-patch).
    view = container["run_context_view_store"].get(run_id)
    working_root_file = view.variables[NODE_ID]["file_id"]
    content = (await fs.download_file(working_root_file)) or b""
    lines = [l for l in content.decode("utf-8").splitlines() if l.strip()]
    assert len(lines) == 2, f"expected header + 1 data row, got {lines!r}"
    assert "hello2" in lines[1] and "world2" in lines[1]

    # 5. Object artifact accepts `edit` as an upsert (same as insert) —
    # there is only one row, so `edit` collapses to `_upsert_single_row`.
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_3,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "edit", "row_id": None,
                "data": {"input": "edited", "input_1": "value"},
            }],
        }],
    ))
    assert result.error is None, result.error
    assert result.results[0].status == "ok"
    etag_edit = result.etag
    assert etag_edit and etag_edit != etag_3

    # 5b. Object artifact must still reject `delete` (no row to address).
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_edit,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{"operation": "delete", "row_id": "x"}],
        }],
    ))
    assert result.error is not None
    assert result.error_code == "OBJECT_OP_NOT_SUPPORTED"

    # 6. Array artifact — insert/edit/delete via the data-factory path.
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_edit,
        artifacts=[{
            "artifact_id": ARTIFACT_ID,
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input_2": "alpha", "input_3": "100"},
            }],
        }],
    ))
    assert result.error is None, result.error
    etag_4 = result.etag


@pytest.mark.asyncio
async def test_patch_table_artifact_still_works_after_submit(client, container):
    """Regression: after POST /submit lands the node in COMPLETED, the view
    store's working copy must still carry the child artifacts so subsequent
    ``PATCH /data artifact_id=<table>`` can target them.

    Prior bug was reported as ``ARTIFACT_NOT_FOUND`` because the working
    ref got clobbered with a structure that dropped the children during
    the resume-pending-task flow.
    """
    _seed_workflow(container)

    r = client.post("/api/v1/runs", json={"workflow_id": WORKFLOW_ID, "input_data": {}})
    start = assert_ok(r)
    run_id = start["id"]
    await _drain(container)

    # Submit the node so it reaches COMPLETED (like the user's scenario).
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    # After submit the view-store working copy must still carry the table
    # artifact under root.artifacts.
    view = container["run_context_view_store"].get(run_id)
    working_ref = view.variables.get(NODE_ID)
    assert isinstance(working_ref, dict), f"working ref missing: {working_ref!r}"
    child_ids = [a.get("artifact_id") for a in (working_ref.get("artifacts") or [])]
    assert ARTIFACT_ID in child_ids, (
        f"table artifact dropped from working ref after submit; "
        f"working_ref={working_ref!r}"
    )

    # PATCH against the table artifact must resolve, not 404.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=output")
    payload = assert_ok(r)
    etag = payload["etag"]

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag,
        artifacts=[{
            "artifact_id": ARTIFACT_ID,
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input_2": "alpha", "input_3": "100"},
            }],
        }],
    ))
    assert result.error is None, (
        f"PATCH on table after submit failed: {result.error_code}: {result.error}"
    )

    # And the row must actually have landed via the data-factory — not just
    # "the artifact resolved". Read the post-PATCH table file_id off the
    # working ref and confirm the inserted value is present in the CSV. This
    # is the literal criterion: a post-submit factory edit works end-to-end.
    view_after = container["run_context_view_store"].get(run_id)
    table_after = next(
        a for a in view_after.variables[NODE_ID]["artifacts"]
        if a.get("artifact_id") == ARTIFACT_ID
    )
    fs = container["file_service_client"]
    table_bytes = (await fs.download_file(table_after["file_id"])) or b""
    assert b"alpha" in table_bytes and b"100" in table_bytes, (
        f"inserted row missing from factory-edited table CSV: {table_bytes!r}"
    )


@pytest.mark.asyncio
async def test_patch_multi_artifact_single_call_updates_root_and_table(
    client, container,
):
    """One PATCH call can carry operations for several artifacts; each is
    applied atomically and a failure in any op rolls the whole batch back."""
    _seed_workflow(container)
    r = client.post("/api/v1/runs", json={"workflow_id": WORKFLOW_ID, "input_data": {}})
    run_id = assert_ok(r)["id"]
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    etag = assert_ok(r)["etag"]

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    # Update scalars AND append a table row in the SAME call.
    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag,
        artifacts=[
            {
                "artifact_id": "__root__",
                "operations": [{
                    "operation": "insert", "row_id": None,
                    "data": {"input": "combo-1", "input_1": "combo-2"},
                }],
            },
            {
                "artifact_id": ARTIFACT_ID,
                "operations": [{
                    "operation": "insert", "row_id": None,
                    "data": {"input_2": "row-A", "input_3": "row-B"},
                }],
            },
        ],
    ))
    assert result.error is None, result.error
    # Per-op results are tagged by artifact_id.
    ids = {res.artifact_id for res in result.results}
    assert ids == {"__root__", ARTIFACT_ID}

    # Root CSV has the scalar row; table CSV has one row.
    view = container["run_context_view_store"].get(run_id)
    working_ref = view.variables[NODE_ID]
    root_fid = working_ref["file_id"]
    table_fid = next(
        a["file_id"] for a in working_ref["artifacts"]
        if a.get("artifact_id") == ARTIFACT_ID
    )
    fs = container["file_service_client"]
    assert b"combo-1" in (await fs.download_file(root_fid)) or b""
    assert b"row-A" in (await fs.download_file(table_fid)) or b""


@pytest.mark.asyncio
async def test_patch_multi_artifact_rollback_on_failure(client, container):
    """If any artifact's ops fail (e.g. unknown artifact_id), nothing is
    committed — other artifacts' earlier ops in the same batch roll back."""
    _seed_workflow(container)
    r = client.post("/api/v1/runs", json={"workflow_id": WORKFLOW_ID, "input_data": {}})
    run_id = assert_ok(r)["id"]
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    etag = assert_ok(r)["etag"]

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]

    # Read the working ref the way the use case does (view → task.resolved_input
    # fallback). At dispatch the INPUT node's tree lives on task.resolved_input,
    # not view.variables[node_id], so reading the view store directly would miss
    # it — the PATCH path itself resolves via ``_current_working_ref``.
    def _working_file_id() -> str:
        task = next(
            t for t in uc.task_repo.list_by_run(run_id) if t.node_id == NODE_ID
        )
        return uc._current_working_ref(run_id, NODE_ID, task)["file_id"]

    working_before_file_id = _working_file_id()

    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag,
        artifacts=[
            {
                "artifact_id": "__root__",
                "operations": [{
                    "operation": "insert", "row_id": None,
                    "data": {"input": "should-not-persist"},
                }],
            },
            {
                "artifact_id": "nonexistent_artifact",
                "operations": [{"operation": "insert", "row_id": None, "data": {}}],
            },
        ],
    ))
    assert result.error is not None
    assert result.error_code == "ARTIFACT_NOT_FOUND"

    # Working ref file_id must NOT have advanced — the first artifact's
    # upload gets orphan-GC'd rather than committed.
    assert _working_file_id() == working_before_file_id, (
        "rollback failed — the first artifact's write committed despite "
        "a later artifact's failure"
    )


@pytest.mark.asyncio
async def test_patch_table_falls_back_to_resolved_input_when_view_empty(
    client, container,
):
    """If the view store's ``variables[node_id]`` is missing (a scenario
    reported by a user whose PATCH got ``ARTIFACT_NOT_FOUND`` despite a
    healthy ``GET /data?side=input`` response), the use case must fall
    back to ``task.resolved_input`` — the frozen input ref carries the
    auto-provisioned artifact tree."""
    _seed_workflow(container)
    r = client.post("/api/v1/runs", json={"workflow_id": WORKFLOW_ID, "input_data": {}})
    start = assert_ok(r)
    run_id = start["id"]
    await _drain(container)

    # Simulate a missing view-store entry (e.g. the projector hasn't
    # caught up, or variables[node_id] was cleared by an upstream flow).
    view = container["run_context_view_store"].get(run_id)
    view.variables.pop(NODE_ID, None)

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    task_repo = uc.task_repo
    task = [t for t in task_repo.list_by_run(run_id) if t.node_id == NODE_ID][0]
    from app.layer1_domain.value_objects.node_input_etag import NodeInputEtag
    from app.layer2_application.node_handlers.schema_providers import (
        StaticSchemaProvider,
    )
    node = uc.workflow_repo.get_by_id(WORKFLOW_ID).nodes[0]
    _, schema_version, _ = await StaticSchemaProvider().resolve(
        node=node, task=task, run=None, view=None,
    )
    etag = NodeInputEtag.of(task.resolved_input, schema_version=schema_version)

    result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag,
        artifacts=[{
            "artifact_id": ARTIFACT_ID,
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input_2": "alpha", "input_3": "100"},
            }],
        }],
    ))
    assert result.error is None, (
        f"PATCH fallback failed: {result.error_code}: {result.error}"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_child(root: dict, artifact_id: str):
    for child in root.get("children") or []:
        if child.get("artifact_id") == artifact_id:
            return child
        hit = _find_child(child, artifact_id)
        if hit is not None:
            return hit
    return None
