"""End-to-end DATA_EDIT round-trip via TestClient.

Validates the full HTTP flow that the FE exercises for DATA_EDIT:

    POST /api/v1/files                          -> upload upstream CSV
    POST /api/v1/workflows                      -> create workflow (auto-provisions test_run)
    POST /api/v1/workflows/{id}/test/full       -> start test run with {file_id: <upstream>}
    GET  /api/v1/runs/{run}/nodes/{n}/data      -> get etag (working ref seeded from upstream)
    PATCH /api/v1/runs/{run}/nodes/{n}/data     -> edit a row in table_data (If-Match)
    POST /api/v1/runs/{run}/nodes/{n}/submit    -> finalize DATA_EDIT
    GET  /api/v1/runs/{run}                     -> assert run COMPLETED

Then asserts the actual CSV bytes downstream — the file_id stored under
``view.variables[data-1]`` must point at the *post-PATCH* CSV (with the
edit applied), distinct from the original upstream file_id.

Note on lifecycle: the upstream file_id is the *upstream node's* output
(shared state — still referenced by ``view.variables[source_node_id]``),
not something DATA_EDIT owns. So the edit_node_data use case clones-on-edit:
it publishes a *new* edited file_id and **preserves the original upstream
file**. Only superseded working-copy files from prior PATCHes are GC'd, never
the node's immutable input.
"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from app.layer1_domain.value_objects.task_status import TaskStatus
from tests.conftest import assert_ok, assert_error
from tests.helpers.queue_runner import QueueRunner


DATA_EDIT_NODE_ID = "data-1"
OUTPUT_NODE_ID = "n_output"


WORKFLOW_PAYLOAD = {
    "name": "data-edit-e2e",
    "description": "DATA_EDIT -> OUTPUT round-trip",
    "nodes": [
        {
            "id": DATA_EDIT_NODE_ID,
            "name": "Edit Orders",
            "node_type": "DATA_EDIT",
            "input_binding": {"file_id": "{{$input.file_id}}"},
            "config": {"instruction": "Review and edit the orders"},
        },
        {
            "id": OUTPUT_NODE_ID,
            "name": "Output",
            "node_type": "OUTPUT",
            # Bind to a literal so the run drains regardless of variable
            # resolution — this test's invariants are about DATA_EDIT,
            # not OUTPUT data flow.
            "input_binding": {"result": "ok"},
            "config": {
                "output_schema": [
                    {"key": "result", "type": "string", "label": "Result"},
                ],
            },
        },
    ],
    "edges": [
        {"id": "e1", "source_node_id": DATA_EDIT_NODE_ID, "target_node_id": OUTPUT_NODE_ID},
    ],
}


@pytest.fixture
def container():
    c = build_app_container()
    # The PATCH /data endpoint enqueues an EditNodeDataAction handled by
    # the OrchestrationWorker, but tests run no worker — only the
    # QueueRunner helper, which doesn't dispatch EditNodeDataAction.
    # Drop the queue + registry so the controller's fallback (direct
    # use-case call) runs synchronously.
    c.pop("_orchestration_queue", None)
    c.pop("_orchestration_response_registry", None)
    return c


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


async def _drain(container) -> None:
    engine = container["_orchestration_worker"]._engine
    queue = container["_orchestration_worker"]._queue
    await QueueRunner(engine, queue).run_until_idle()


async def _upload_orders_csv(container) -> str:
    """Upload a 2-row orders CSV directly through the file_service_client
    (TestClient doesn't include a file route here). Returns the file_id."""
    fs = container["file_service_client"]
    # Standard ``__row_id`` column-prefix matches the row-transform
    # service's expectations (see csv_ops.ROW_ID_COLUMN).
    csv_bytes = b"__row_id,sku,qty\nr1,SKU-A,5\nr2,SKU-B,7\n"
    up = await fs.upload_file(csv_bytes, "orders.csv", "text/csv")
    return up["file_id"]


def _create_workflow(client) -> str:
    r = client.post("/api/v1/workflows", json=WORKFLOW_PAYLOAD)
    body = assert_ok(r)
    return body["id"]


def _start_test_run(client, workflow_id: str, *, input_data: dict) -> str:
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": input_data},
    )
    return assert_ok(r)["run_id"]


def _patch_artifact(client, run_id: str, etag: str, *, operations: list[dict]):
    """Generic helper — issues PATCH /data with the given operations
    against ``table_data`` and the supplied If-Match header."""
    return client.patch(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/data",
        headers={"If-Match": etag},
        json={"artifacts": [{
            "artifact_id": "table_data",
            "operations": operations,
        }]},
    )


def _patch_table_row(client, run_id: str, etag: str, *, row_id: str, data: dict):
    return client.patch(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/data",
        headers={"If-Match": etag},
        json={"artifacts": [{
            "artifact_id": "table_data",
            "operations": [{
                "operation": "edit",
                "row_id": row_id,
                "data": data,
            }],
        }]},
    )


@pytest.mark.asyncio
async def test_data_edit_round_trip_propagates_edit_to_node_output(client, container):
    """Dispatch → GET → PATCH (edit row 1) → submit → COMPLETED. The
    file stored under ``variables[data-1]`` must contain the edit, while
    the upstream file is unchanged."""
    upstream_file_id = await _upload_orders_csv(container)
    workflow_id = _create_workflow(client)

    run_id = _start_test_run(
        client, workflow_id,
        input_data={"file_id": upstream_file_id},
    )
    await _drain(container)

    # GET /data → working ref seeded from upstream.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/data")
    g = assert_ok(r)
    etag_initial = g["etag"]
    assert etag_initial, "DATA_EDIT must report a real etag, not the empty placeholder"

    # PATCH /data — edit row r1's qty from 5 to 999.
    pr = _patch_table_row(
        client, run_id, etag_initial,
        row_id="r1", data={"qty": "999"},
    )
    p = assert_ok(pr)
    assert p["etag"] != etag_initial, "PATCH must mint a new etag"
    assert p["version"] == 1

    # Stale etag on second PATCH -> 412.
    pr_stale = _patch_table_row(
        client, run_id, etag_initial,
        row_id="r1", data={"qty": "1"},
    )
    assert pr_stale.status_code == 412, (
        f"stale If-Match must 412; got {pr_stale.status_code} "
        f"body={pr_stale.json()!r}"
    )
    assert_error(pr_stale, code="PRECONDITION_FAILED")

    # Submit -> COMPLETED.
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/submit")
    sub = assert_ok(r)
    assert sub["node_state"] == "COMPLETED"
    await _drain(container)

    # Run must have drained (single non-OUTPUT pending node).
    r = client.get(f"/api/v1/runs/{run_id}")
    run_view = assert_ok(r)
    assert run_view["status"] == "COMPLETED", (
        f"run did not drain; got {run_view['status']!r}"
    )

    # Inspect view.variables[data-1] — must be the post-PATCH file_ref.
    view = container["run_context_view_store"].get(run_id)
    output_ref = view.variables.get(DATA_EDIT_NODE_ID)
    assert output_ref is not None, "DATA_EDIT must register an output ref"
    assert output_ref.get("__file_ref") is True, (
        "DATA_EDIT output must be a file_ref tree, not a plain dict"
    )

    artifacts = output_ref.get("artifacts") or []
    assert len(artifacts) == 1, "exactly one nested artifact (table_data)"
    edited_table = artifacts[0]
    assert edited_table["name"] == "table_data"
    edited_file_id = edited_table["file_id"]
    assert edited_file_id, "table_data file_id must be set after PATCH"
    assert edited_file_id != upstream_file_id, (
        "DATA_EDIT must produce a new file_id (clone-on-edit); the "
        "upstream file_id must NOT be mutated"
    )

    # Verify the actual CSV bytes carry the edit.
    fs = container["file_service_client"]
    edited_bytes = await fs.download_file(edited_file_id)
    rows = list(csv.DictReader(io.StringIO(edited_bytes.decode("utf-8"))))
    assert len(rows) == 2, f"expected 2 rows after edit; got {rows!r}"
    by_row = {r["__row_id"]: r for r in rows}
    assert by_row["r1"]["qty"] == "999", (
        f"row r1 must reflect the PATCH edit; got {by_row['r1']!r}"
    )
    assert by_row["r2"]["qty"] == "7", (
        f"row r2 must be untouched; got {by_row['r2']!r}"
    )

    # The upstream file_id is the upstream node's output (shared state),
    # not DATA_EDIT's to delete. PATCH clones-on-edit: it publishes
    # ``edited_file_id`` and MUST preserve the original upstream file so
    # the upstream node's output / reruns / other consumers stay intact.
    upstream_bytes_after = await fs.download_file(upstream_file_id)
    assert upstream_bytes_after is not None, (
        "edit_node_data must NOT delete the upstream source file — it "
        "belongs to the upstream node's output, not this node's working copy"
    )

    # input_file_id must NOT be set on the task — DATA_EDIT skips that
    # CSV roundtrip because the file_ref tree IS the input artifact.
    task_id = sub["task_id"]
    task = container["get_task"].task_repo.get_by_id(task_id)
    assert "input_file_id" not in (task.output_metadata or {}), (
        "DATA_EDIT must NOT store an input_file_id (file_ref is the input)"
    )


# ---------------------------------------------------------------------------
# Column-mutating ops (add_column / drop_column / rename_column)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_column_op_round_trip_changes_schema_and_data(client, container):
    """Column-level PATCH ops must:

    1. Mutate ``view.variables[node].artifacts[0].columns`` immediately so
       the descriptor tree (GET /data) reflects the new column set.
    2. Make ``GET /schema`` re-derive its schema from the live columns and
       bump its content-hash version on every change.
    3. Round-trip atomically with row ops in the same PATCH (chained
       file_id + intermediate GC).
    4. Reject reserved/duplicate column names with structured errors.
    """
    upstream_file_id = await _upload_orders_csv(container)
    workflow_id = _create_workflow(client)
    run_id = _start_test_run(
        client, workflow_id, input_data={"file_id": upstream_file_id},
    )
    await _drain(container)

    def _table_columns(schema_payload) -> list[str]:
        """Pull cell keys out of an auto-form table-array schema. Columns
        sit at ``schema[0].fields[0].fields[i].key`` — the outer dict is
        the array wrapper, ``fields[0]`` is the row template, and its
        ``fields`` is the list of cells."""
        if not schema_payload:
            return []
        rows = schema_payload[0].get("fields") or []
        if not rows:
            return []
        cells = rows[0].get("fields") or []
        return [c.get("key") for c in cells if c.get("key")]

    # GET /schema before any PATCH — captures the dispatch-time version.
    sresp = client.get(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/schema",
    )
    s_v1 = assert_ok(sresp)
    cols_v1 = _table_columns(s_v1["schema"])
    assert cols_v1 == ["sku", "qty"], (
        f"dispatch-time schema must mirror upstream headers; got {cols_v1!r}"
    )
    schema_v1_version = s_v1["schema_version"]
    assert schema_v1_version.startswith("sha256:")

    # GET /data → grab the etag for our first PATCH.
    g = assert_ok(client.get(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/data",
    ))
    etag = g["etag"]

    # PATCH 1 — add_column "region" with a default value.
    pr = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "add_column",
        "column": "region",
        "default_value": "US",
    }])
    p = assert_ok(pr)
    etag = p["etag"]

    # GET /schema → version must change, columns must include "region".
    s_v2 = assert_ok(client.get(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/schema",
    ))
    cols_v2 = _table_columns(s_v2["schema"])
    assert cols_v2 == ["sku", "qty", "region"], (
        f"GET /schema must reflect add_column immediately; got {cols_v2!r}"
    )
    assert s_v2["schema_version"] != schema_v1_version

    # GET /data?side=output → descriptor tree's table_data.columns must
    # reflect the post-PATCH state. (Default ``side=input`` returns the
    # frozen dispatch-time ref, which is correct but irrelevant here.)
    d_v2 = assert_ok(client.get(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/data?side=output",
    ))
    table_artifact = d_v2["output"]["root"]["children"][0]
    assert table_artifact["artifact_id"] == "table_data"
    assert "region" in table_artifact["columns"]

    # PATCH 2 — insert a new row that uses the new column.
    pr = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "insert", "row_id": None,
        "data": {"sku": "SKU-C", "qty": "11", "region": "EU"},
    }])
    etag = assert_ok(pr)["etag"]

    # PATCH 3 — rename column qty -> quantity.
    pr = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "rename_column", "column": "qty", "new_name": "quantity",
    }])
    etag = assert_ok(pr)["etag"]

    s_v3 = assert_ok(client.get(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/schema",
    ))
    cols_v3 = _table_columns(s_v3["schema"])
    assert "qty" not in cols_v3 and "quantity" in cols_v3, cols_v3

    # PATCH 4 — drop_column "region".
    pr = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "drop_column", "column": "region",
    }])
    etag = assert_ok(pr)["etag"]

    # Submit + drain → run completes with the column-mutated table.
    sub = assert_ok(client.post(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/submit",
    ))
    assert sub["node_state"] == "COMPLETED"
    await _drain(container)
    rv = assert_ok(client.get(f"/api/v1/runs/{run_id}"))
    assert rv["status"] == "COMPLETED", rv

    # Inspect the final CSV bytes — header reflects every column op.
    view = container["run_context_view_store"].get(run_id)
    output_ref = view.variables.get(DATA_EDIT_NODE_ID)
    final_table = output_ref["artifacts"][0]
    assert set(final_table["columns"]) == {"sku", "quantity"}, (
        f"final columns must reflect rename + drop; got {final_table['columns']!r}"
    )
    fs = container["file_service_client"]
    final_bytes = await fs.download_file(final_table["file_id"])
    rows = list(csv.DictReader(io.StringIO(final_bytes.decode("utf-8"))))
    # Original 2 rows + 1 inserted = 3 rows.
    assert len(rows) == 3, f"expected 3 rows after column round trip; got {rows!r}"
    # Every row must carry the renamed key, not the old "qty".
    assert all("quantity" in r and "qty" not in r for r in rows), rows
    # The inserted row's quantity survived the rename.
    inserted = [r for r in rows if r.get("sku") == "SKU-C"]
    assert len(inserted) == 1 and inserted[0]["quantity"] == "11", inserted


@pytest.mark.asyncio
async def test_column_op_rejects_reserved_and_duplicate_names(client, container):
    """Negative cases — reserved + duplicate column names produce
    structured 400s with the documented error codes."""
    upstream_file_id = await _upload_orders_csv(container)
    workflow_id = _create_workflow(client)
    run_id = _start_test_run(
        client, workflow_id, input_data={"file_id": upstream_file_id},
    )
    await _drain(container)
    etag = assert_ok(client.get(
        f"/api/v1/runs/{run_id}/nodes/{DATA_EDIT_NODE_ID}/data",
    ))["etag"]

    # Reserved: drop_column on __row_id.
    bad = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "drop_column", "column": "__row_id",
    }])
    assert bad.status_code == 400, bad.json()
    assert_error(bad, code="RESERVED_COLUMN")

    # Duplicate: add_column "sku" (already in upstream headers).
    bad = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "add_column", "column": "sku", "default_value": "x",
    }])
    assert bad.status_code == 400, bad.json()
    assert_error(bad, code="DUPLICATE_COLUMN")

    # Unknown: drop_column on a name that doesn't exist.
    bad = _patch_artifact(client, run_id, etag, operations=[{
        "operation": "drop_column", "column": "nope",
    }])
    assert bad.status_code == 400, bad.json()
    assert_error(bad, code="UNKNOWN_COLUMN")


# ─────────────────── NodeSpec config layouts (Module 6) ───────────────────
#
# The declared DATA_EDIT fields live in ``config`` (canonical home); the
# FE submits them nested under the ``data_edit_tabs`` binding container;
# 042-migrated nodes carry them flat in config. The handler overlays
# config with the flattened binding (binding wins). One test per layout.


def _layout_workflow(nodes_payload: dict) -> dict:
    return {
        "name": "data-edit-layouts",
        "nodes": [
            {"id": DATA_EDIT_NODE_ID, "name": "Edit Orders",
             "node_type": "DATA_EDIT", **nodes_payload},
        ],
        "edges": [],
    }


async def _pending_data_edit_task(client, container, nodes_payload):
    upstream_file_id = await _upload_orders_csv(container)
    r = client.post("/api/v1/workflows", json=_layout_workflow(nodes_payload))
    wf_id = assert_ok(r)["id"]
    run_id = _start_test_run(client, wf_id, input_data={"file_id": upstream_file_id})
    await _drain(container)
    tasks = [t for t in container["_infra"].repos.task.list_by_run(run_id)
             if t.node_id == DATA_EDIT_NODE_ID]
    assert tasks, "DATA_EDIT task missing"
    return tasks[-1], upstream_file_id


@pytest.mark.asyncio
async def test_data_edit_fe_nested_tabs_layout(client, container):
    task, upstream = await _pending_data_edit_task(client, container, {
        "parameters": {
            "data_edit_tabs": {
                "main": {"file_id": "{{$input.file_id}}", "query": "$top=5"},
                "settings_tab": {"readonly": True},
            },
        },
    })
    assert task.status == TaskStatus.PENDING
    meta = task.output_metadata or {}
    assert meta.get("file_id") == upstream
    assert meta.get("query_params") == {"$top": "5"}
    assert meta.get("readonly") is True


@pytest.mark.asyncio
async def test_data_edit_flat_canonical_config_layout(client, container):
    """Post-042 shape: declared fields flat in config (also what flat-key
    API authors produce via the spec split)."""
    task, upstream = await _pending_data_edit_task(client, container, {
        "parameters": {
            "file_id": "{{$input.file_id}}",
            "query": "$top=5",
            "readonly": True,
        },
    })
    assert task.status == TaskStatus.PENDING
    meta = task.output_metadata or {}
    assert meta.get("file_id") == upstream
    assert meta.get("query_params") == {"$top": "5"}
    assert meta.get("readonly") is True


@pytest.mark.asyncio
async def test_data_edit_binding_overrides_migrated_config(client, container):
    """Mixed layout: migrated config values are the base; a fresh FE edit
    (binding side) wins."""
    task, upstream = await _pending_data_edit_task(client, container, {
        "config": {"instruction": "old migrated", "readonly": True},
        "input_binding": {
            "data_edit_tabs": {
                "main": {"file_id": "{{$input.file_id}}",
                         "instruction": "fresh FE edit"},
                "settings_tab": {"readonly": False},
            },
        },
    })
    assert task.status == TaskStatus.PENDING
    meta = task.output_metadata or {}
    assert meta.get("file_id") == upstream
    assert meta.get("instruction") == "fresh FE edit"
    assert meta.get("readonly") is False
