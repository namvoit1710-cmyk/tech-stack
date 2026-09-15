"""Reproduction: INPUT submit → GET /data?side=output.

User report: after a "successful" test run, ``GET /data?side=output`` for a
submitted INPUT node returns ``node_state="PENDING"`` and ``output.root=null``
(``produced_artifacts=[]``).

These tests pin down whether that is a code bug or the expected state after a
test-run RE-EXECUTE (the persistent ``test_run_id`` is reused; ``/test/full``
clears the event log and re-dispatches the INPUT node fresh).

- ``test_submit_then_side_output_is_completed_with_root`` — the happy path:
  provision → PATCH __root__ → submit → side=output must be COMPLETED with a
  file-backed root. If this FAILS, the submit/projection path has a real bug.
- ``test_reexecute_repopulates_side_output_with_fresh_provisioned_ref`` —
  after a successful submit, running ``/test/full`` again re-provisions a
  FRESH working copy on side=output (node PENDING, ``output.root`` populated
  with none of the prior submit's data). A PENDING dispatch now publishes its
  provisioned ref to the projector view fan-out-free, so the output side is
  populated at dispatch (copy-on-write with the input side) rather than the
  empty "PENDING + null" it used to return.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner


INPUT_SCHEMA = [
    {
        "key": "triggerId",
        "outputType": "string",
        "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "fieldControl": "InputControl",
            "wrapperProps": {"label": "Trigger Id"},
        },
    },
    {
        "key": "description",
        "outputType": "string",
        "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "fieldControl": "InputControl",
            "wrapperProps": {"label": "Description"},
        },
    },
    {
        "key": "objectType",
        "outputType": "string",
        "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "fieldControl": "InputControl",
            "wrapperProps": {"label": "Object Type"},
        },
    },
]

WORKFLOW_ID = "wf-input-submit-repro"
NODE_ID = "n_input_repro"


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
        name="Input submit repro",
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


async def _patch_root(container, run_id: str) -> None:
    """Write the three scalar fields into __root__ exactly like the FE PATCH."""
    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    # Read the current working etag the way GET /data does.
    get_uc = container["get_node_data"]
    from app.layer2_application.features.get_node_data.use_cases.get_node_data_use_case import (
        GetNodeDataCommand,
    )
    cur = await get_uc.execute(GetNodeDataCommand(run_id=run_id, node_id=NODE_ID, side="output"))
    uc = container["edit_node_data"]
    res = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=cur.etag,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {
                    "triggerId": "T-1",
                    "description": "hello world",
                    "objectType": "ORDER",
                },
            }],
        }],
    ))
    assert res.error is None, f"PATCH __root__ failed: {res.error_code}: {res.error}"


@pytest.mark.asyncio
async def test_submit_then_side_output_is_completed_with_root(client, container):
    _seed_workflow(container)

    # Use the design-time test run (the same path the user exercises).
    r = client.post(f"/api/v1/workflows/{WORKFLOW_ID}/test/full", json={"input_data": {}})
    assert_ok(r)
    await _drain(container)

    # Resolve the test run id.
    wf = container["get_node_schema"].workflow_repo.get_by_id(WORKFLOW_ID)
    run_id = wf.test_run_id
    assert run_id

    # PATCH the root scalars, then submit.
    await _patch_root(container, run_id)
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    # GET side=output — the criterion the user observed failing.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=output")
    payload = assert_ok(r)

    assert payload["node_state"] == "COMPLETED", (
        f"expected COMPLETED, got {payload['node_state']!r}; payload={payload}"
    )
    out = payload["output"]
    assert out is not None
    assert out["root"] is not None, f"output.root is null after submit; payload={payload}"
    assert out["root"]["file_id"], f"output.root has no file_id; root={out['root']}"

    # Clincher: the RUN itself is genuinely COMPLETED here (single INPUT leaf).
    # This is what "run success" should look like — node COMPLETED + run
    # COMPLETED + output.root populated, all consistent.
    run_status = container["run_context_view_store"].get(run_id).status
    assert run_status == "COMPLETED", f"expected run COMPLETED, got {run_status!r}"


@pytest.mark.asyncio
async def test_reexecute_repopulates_side_output_with_fresh_provisioned_ref(client, container):
    """After a successful submit, re-executing ``/test/full`` re-provisions a
    FRESH working copy on ``side=output`` for the re-dispatched INPUT node.

    Node_state is PENDING (awaiting input again) and ``output.root`` is
    populated — copy-on-write with the freshly provisioned input side — and
    carries NONE of the prior run's submitted data. This supersedes the earlier
    "side=output resets to PENDING + null" behaviour: a PENDING dispatch now
    publishes its provisioned ref to the projector view fan-out-free (see
    ``NodeExecutionRunner._run_pending``), so the FE can render / PATCH the
    working copy immediately instead of seeing an empty slot until the first
    edit. The user's "did my submit get lost?" question is answered by the
    output being a fresh empty form, not the old values.
    """
    _seed_workflow(container)

    # Cycle 1: run, patch, submit → COMPLETED with output.
    r = client.post(f"/api/v1/workflows/{WORKFLOW_ID}/test/full", json={"input_data": {}})
    assert_ok(r)
    await _drain(container)
    wf = container["get_node_schema"].workflow_repo.get_by_id(WORKFLOW_ID)
    run_id = wf.test_run_id
    await _patch_root(container, run_id)
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=output")
    p1 = assert_ok(r)
    assert p1["node_state"] == "COMPLETED"
    assert p1["output"]["root"] is not None
    submitted_file_id = p1["output"]["root"]["file_id"]

    # Cycle 2: re-execute the SAME persistent test run.
    r = client.post(f"/api/v1/workflows/{WORKFLOW_ID}/test/full", json={"input_data": {}})
    assert_ok(r)
    await _drain(container)

    # Same run_id, freshly re-dispatched INPUT node.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=output")
    p2 = assert_ok(r)
    assert p2["node_state"] == "PENDING", (
        f"expected PENDING after re-execute, got {p2['node_state']!r}"
    )
    # Output side is re-populated with the FRESH provisioned ref — not null,
    # and not the prior run's submitted file.
    assert p2["output"]["root"] is not None, (
        f"expected output.root re-provisioned after re-execute, got "
        f"{p2['output']['root']!r}"
    )
    fresh_file_id = p2["output"]["root"]["file_id"]
    assert fresh_file_id and fresh_file_id != submitted_file_id, (
        f"output.root must point at a fresh provisioned file, not the prior "
        f"run's submitted file {submitted_file_id!r}; got {fresh_file_id!r}"
    )
    # The fresh working copy must not carry the previous run's submitted values.
    fs = container["file_service_client"]
    body = (await fs.download_file(fresh_file_id)) or b""
    assert b"hello world" not in body, (
        f"re-provisioned working copy still contains the prior submit; "
        f"body={body!r}"
    )
    assert p2["produced_artifacts"] == []

    # The run is back to PENDING/RUNNING, awaiting input on the freshly
    # re-dispatched INPUT node — a re-execute that re-provisions, not a lost
    # submit.
    run_status = container["run_context_view_store"].get(run_id).status
    assert run_status in ("PENDING", "RUNNING"), (
        f"expected run awaiting input (PENDING/RUNNING), got {run_status!r}"
    )
