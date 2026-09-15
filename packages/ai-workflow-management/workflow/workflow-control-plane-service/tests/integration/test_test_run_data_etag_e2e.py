"""End-to-end: data + etag chain across two test-run executions.

Walks the user-facing flow over HTTP via TestClient:

    POST /api/v1/workflows                     -> create workflow (auto-provisions test_run)
    POST /api/v1/workflows/{id}/test/full      -> start test run #1
    GET  /api/v1/runs/{run}/nodes/{n}/data     -> get etag
    PATCH /api/v1/runs/{run}/nodes/{n}/data    -> submit input (If-Match)
    POST /api/v1/runs/{run}/nodes/{n}/submit   -> finalize INPUT
    POST /api/v1/workflows/{id}/test/full      -> start test run #2 (resets state)
    ... same INPUT cycle ...

Asserts the etag chain restarts cleanly after the test-run reset and that
the negative paths (stale etag across reset, missing If-Match, replayed
etag in one run) fail with 412 / 409 as documented.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok, assert_error
from tests.helpers.queue_runner import QueueRunner


INPUT_NODE_ID = "n_input"
OUTPUT_NODE_ID = "n_output"

INPUT_SCHEMA = [
    {
        "key": "input",
        "outputType": "string",
        "fieldConfig": {
            "fieldWrapper": "FormItemWrapper",
            "fieldControl": "InputControl",
            "wrapperProps": {"label": "Input"},
        },
    },
]

# INPUT -> OUTPUT. OUTPUT binds to a literal so the run completes
# regardless of variable-resolver path semantics — this test's invariants
# are about the etag/PATCH chain on the INPUT node, not about data
# propagation through OUTPUT.
WORKFLOW_PAYLOAD = {
    "name": "etag-e2e",
    "description": "INPUT -> OUTPUT chain for etag e2e test",
    "nodes": [
        {
            "id": INPUT_NODE_ID,
            "name": "Input",
            "node_type": "INPUT",
            "config": {"input_schema": INPUT_SCHEMA},
        },
        {
            "id": OUTPUT_NODE_ID,
            "name": "Output",
            "node_type": "OUTPUT",
            "input_binding": {"result": "ok"},
            "config": {
                "output_schema": [
                    {"key": "result", "type": "string", "label": "Result"},
                ],
            },
        },
    ],
    "edges": [
        {"id": "e1", "source_node_id": INPUT_NODE_ID, "target_node_id": OUTPUT_NODE_ID},
    ],
}


@pytest.fixture
def container():
    c = build_app_container()
    # The PATCH /data endpoint enqueues an EditNodeDataAction handled by
    # the OrchestrationWorker, but tests run no worker — only the
    # QueueRunner helper, which doesn't dispatch EditNodeDataAction.
    # Drop the queue + registry so the controller's fallback (direct
    # use-case call) runs synchronously. Engine-side fan-out still uses
    # the worker's queue ref via container["_orchestration_worker"]._queue.
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


def _create_workflow(client) -> str:
    r = client.post("/api/v1/workflows", json=WORKFLOW_PAYLOAD)
    body = assert_ok(r)
    assert body["test_run_id"], "create-workflow must auto-provision test_run_id"
    assert {INPUT_NODE_ID, OUTPUT_NODE_ID} <= {n["id"] for n in body["nodes"]}
    return body["id"]


def _start_test_run(client, workflow_id: str) -> str:
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    return assert_ok(r)["run_id"]


def _patch_input(client, run_id: str, etag: str | None, value: str):
    headers = {"If-Match": etag} if etag is not None else {}
    return client.patch(
        f"/api/v1/runs/{run_id}/nodes/{INPUT_NODE_ID}/data",
        headers=headers,
        json={"artifacts": [{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert",
                "row_id": None,
                "data": {"input": value},
            }],
        }]},
    )


@pytest.mark.asyncio
async def test_test_run_two_cycles_data_etag_chain(client, container):
    """Two /test/full cycles on the same workflow with the same input.

    Run #1: GET etag -> PATCH (version=1, new etag) -> submit -> drain.
    Run #2: same flow. After reset the working tree is freshly minted,
    so the new etag MUST differ from any etag observed in run #1 and
    PATCH's version counter restarts at 1.
    """
    workflow_id = _create_workflow(client)

    # --- Run #1 --------------------------------------------------------
    run_id_1 = _start_test_run(client, workflow_id)
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id_1}/nodes/{INPUT_NODE_ID}/data")
    g1 = assert_ok(r)
    etag_run1_initial = g1["etag"]
    assert etag_run1_initial.startswith("sha256:")
    assert g1["node_state"] == "PENDING"
    assert r.headers["ETag"] == f'W/"{etag_run1_initial}"'

    pr1 = _patch_input(client, run_id_1, etag_run1_initial, value="7")
    patch1 = assert_ok(pr1)
    etag_after_patch1 = patch1["etag"]
    assert patch1["version"] == 1
    assert etag_after_patch1 != etag_run1_initial
    assert pr1.headers["ETag"] == f'W/"{etag_after_patch1}"'

    r = client.post(f"/api/v1/runs/{run_id_1}/nodes/{INPUT_NODE_ID}/submit")
    submit1 = assert_ok(r)
    assert submit1["node_state"] == "COMPLETED"
    await _drain(container)

    view_1 = container["run_context_view_store"].get(run_id_1)
    assert OUTPUT_NODE_ID in view_1.completed_nodes, (
        f"OUTPUT didn't complete in run #1; "
        f"completed_nodes={view_1.completed_nodes!r}"
    )

    # --- Run #2 (same workflow_id, same test_run_id) -------------------
    run_id_2 = _start_test_run(client, workflow_id)
    assert run_id_2 == run_id_1, (
        "test/full reuses the permanent test_run_id (1:1 with workflow)"
    )
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id_2}/nodes/{INPUT_NODE_ID}/data")
    g2 = assert_ok(r)
    etag_run2_initial = g2["etag"]
    assert g2["node_state"] == "PENDING", (
        f"INPUT must be PENDING after reset; got {g2['node_state']!r}"
    )
    assert etag_run2_initial != etag_run1_initial, (
        "fresh provision after reset must mint a new etag root"
    )
    assert etag_run2_initial != etag_after_patch1, (
        "run #1's post-PATCH etag must NOT survive into run #2"
    )

    pr2 = _patch_input(client, run_id_2, etag_run2_initial, value="7")
    patch2 = assert_ok(pr2)
    assert patch2["version"] == 1, (
        "version restarts after reset (new working tree, version=0 base)"
    )
    etag_after_patch2 = patch2["etag"]
    assert etag_after_patch2 != etag_after_patch1

    r = client.post(f"/api/v1/runs/{run_id_2}/nodes/{INPUT_NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    view_2 = container["run_context_view_store"].get(run_id_1)
    assert OUTPUT_NODE_ID in view_2.completed_nodes, (
        f"OUTPUT didn't complete in run #2; "
        f"completed_nodes={view_2.completed_nodes!r}"
    )


@pytest.mark.asyncio
async def test_patch_with_stale_etag_after_test_run_reset_returns_412(client, container):
    """An etag captured in run #1 must not satisfy If-Match in run #2."""
    workflow_id = _create_workflow(client)

    run_id = _start_test_run(client, workflow_id)
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{INPUT_NODE_ID}/data")
    stale_etag = assert_ok(r)["etag"]

    pr = _patch_input(client, run_id, stale_etag, value="3")
    assert_ok(pr)
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{INPUT_NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    run_id_2 = _start_test_run(client, workflow_id)
    assert run_id_2 == run_id
    await _drain(container)

    pr_stale = _patch_input(client, run_id, stale_etag, value="3")
    assert pr_stale.status_code == 412, (
        f"expected 412 for stale-across-reset etag; got {pr_stale.status_code} "
        f"body={pr_stale.json()!r}"
    )
    assert_error(pr_stale, code="PRECONDITION_FAILED")


@pytest.mark.asyncio
async def test_patch_without_if_match_returns_409(client, container):
    """Missing If-Match header must yield 409 PRECONDITION_REQUIRED."""
    workflow_id = _create_workflow(client)
    run_id = _start_test_run(client, workflow_id)
    await _drain(container)

    r = client.patch(
        f"/api/v1/runs/{run_id}/nodes/{INPUT_NODE_ID}/data",
        json={"artifacts": [{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "1"},
            }],
        }]},
    )
    assert r.status_code == 409, (
        f"expected 409 for missing If-Match; got {r.status_code} body={r.json()!r}"
    )
    assert_error(r, code="PRECONDITION_REQUIRED")


@pytest.mark.asyncio
async def test_patch_with_replayed_etag_in_same_run_returns_412(client, container):
    """Within one run, PATCH-2 reusing PATCH-1's pre-image etag must 412."""
    workflow_id = _create_workflow(client)
    run_id = _start_test_run(client, workflow_id)
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{INPUT_NODE_ID}/data")
    etag_v0 = assert_ok(r)["etag"]

    pr1 = _patch_input(client, run_id, etag_v0, value="2")
    assert_ok(pr1)

    pr2 = _patch_input(client, run_id, etag_v0, value="3")
    assert pr2.status_code == 412, (
        f"expected 412 for replayed etag in same run; got {pr2.status_code} "
        f"body={pr2.json()!r}"
    )
    assert_error(pr2, code="PRECONDITION_FAILED")


# ── Custom INPUT-form workflow: test/full -> test_node -> test/full ──
#
# Payload mirrors the FE-authored shape (parameters + JSON-string
# input_schema, scalars + nested table artifact). Confirms that:
#   - WorkflowNode coerces parameters -> config and the INPUT handler
#     parses the string-encoded input_schema lazily at dispatch.
#   - All three calls reuse the same permanent test_run_id while bumping
#     generation and minting fresh etag roots — so the etag chain
#     restarts cleanly between calls regardless of which endpoint was
#     used to dispatch.

CUSTOM_INPUT_NODE_ID = "60e20512-6fce-4d99-8e8a-ed0c5ef491ab"
CUSTOM_INPUT_SCHEMA_JSON = (
    "[\r\n"
    "  {\r\n    \"key\": \"input\",\r\n    \"outputType\": \"string\",\r\n"
    "    \"fieldConfig\": {\r\n      \"fieldWrapper\": \"FormItemWrapper\",\r\n"
    "      \"fieldControl\": \"InputControl\",\r\n"
    "      \"wrapperProps\": {\"label\": \"Input 1\"}\r\n    }\r\n  },\r\n"
    "  {\r\n    \"key\": \"input_1\",\r\n    \"outputType\": \"string\",\r\n"
    "    \"fieldConfig\": {\r\n      \"fieldWrapper\": \"FormItemWrapper\",\r\n"
    "      \"fieldControl\": \"InputControl\",\r\n"
    "      \"wrapperProps\": {\"label\": \"Input 2\"}\r\n    }\r\n  },\r\n"
    "  {\r\n    \"key\": \"table_3_lg2h0\",\r\n    \"outputType\": \"array\",\r\n"
    "    \"fieldConfig\": {\r\n      \"fieldWrapper\": \"OdataWrapper\",\r\n"
    "      \"wrapperProps\": {\"label\": \"Table\"},\r\n"
    "      \"controlProps\": {}\r\n    },\r\n"
    "    \"fields\": [\r\n      {\r\n"
    "        \"key\": \"table_3_lg2h0_row\",\r\n        \"outputType\": \"object\",\r\n"
    "        \"fieldConfig\": {\"fieldWrapper\": \"FragmentWrapper\", \"wrapperProps\": {}},\r\n"
    "        \"fields\": [\r\n"
    "          {\"key\": \"input_2\", \"outputType\": \"string\","
    " \"fieldConfig\": {\"fieldWrapper\": \"FormItemWrapper\","
    " \"fieldControl\": \"InputControl\", \"wrapperProps\": {\"label\": \"col A\"}}},\r\n"
    "          {\"key\": \"input_3\", \"outputType\": \"string\","
    " \"fieldConfig\": {\"fieldWrapper\": \"FormItemWrapper\","
    " \"fieldControl\": \"InputControl\", \"wrapperProps\": {\"label\": \"col B\"}}}\r\n"
    "        ]\r\n      }\r\n    ]\r\n  }\r\n]"
)

CUSTOM_WORKFLOW_PAYLOAD = {
    "name": "test input form",
    "description": "test input form",
    "nodes": [
        {
            "id": CUSTOM_INPUT_NODE_ID,
            "name": "custom input form",
            "node_type": "INPUT",
            "worker_type": "builtin:input",
            # FE-authored shape: parameters + JSON-string input_schema.
            # WorkflowNode.__post_init__ -> split_parameters() routes
            # input_schema/instruction into node.config; the INPUT handler
            # parses the string lazily at dispatch.
            "parameters": {
                "instruction": "input fields",
                "input_schema": CUSTOM_INPUT_SCHEMA_JSON,
            },
            "x": -1747,
            "y": -1018.5,
            "description": "Pauses workflow to collect structured input from a user.",
            "icon": "FormInput",
            "color": "#8B5CF6",
            "ports": {
                "in": [{"id": "in_default", "label": "Input", "data_type": "any",
                        "required": True, "description": "", "readonly": True}],
                "out": [{"id": "out_default", "label": "Output", "data_type": "any",
                         "required": True, "description": "", "readonly": True}],
            },
            "node_definition_id": None,
            "editable": True,
        },
    ],
    "edges": [],
}


def _create_custom_workflow(client) -> str:
    r = client.post("/api/v1/workflows", json=CUSTOM_WORKFLOW_PAYLOAD)
    body = assert_ok(r)
    assert body["test_run_id"]
    assert any(n["id"] == CUSTOM_INPUT_NODE_ID for n in body["nodes"])
    return body["id"]


def _patch_custom_input(client, run_id: str, etag: str, value: str):
    return client.patch(
        f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data",
        headers={"If-Match": etag},
        json={"artifacts": [{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert",
                "row_id": None,
                "data": {"input": value, "input_1": value + "-b"},
            }],
        }]},
    )


@pytest.mark.asyncio
async def test_full_then_node_then_full_etag_chain_restarts_each_step(client, container):
    """Sequence: test/full -> nodes/{id}/test -> test/full.

    Each call reuses the workflow's permanent test_run_id, bumps
    generation, and clears the event log. The etag tree is freshly
    minted at every step, so:
      * GET /data after each step yields a NEW etag distinct from every
        etag observed in the previous steps.
      * PATCH succeeds with the fresh etag (version restarts at 1).
      * PATCH using a previous step's etag returns 412.
    """
    workflow_id = _create_custom_workflow(client)

    # ── Step 1: POST /test/full ────────────────────────────────────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    full1 = assert_ok(r)
    run_id = full1["run_id"]
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g1 = assert_ok(r)
    assert g1["node_state"] == "PENDING"
    # Identity-only descriptor tree: scalars CSV + table artifact child.
    assert g1["data"]["root"] is not None
    etag_step1_initial = g1["etag"]

    pr = _patch_custom_input(client, run_id, etag_step1_initial, value="alpha")
    p1 = assert_ok(pr)
    assert p1["version"] == 1
    etag_step1_after_patch = p1["etag"]
    assert etag_step1_after_patch != etag_step1_initial

    # ── Step 2: POST /nodes/{id}/test (test_node) ─────────────────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/{CUSTOM_INPUT_NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    node_res = assert_ok(r)
    assert node_res["run_id"] == run_id, (
        "test_node must reuse the workflow's permanent test_run_id"
    )
    assert node_res["execution_type"] == "pending_input"
    await _drain(container)

    # Re-dispatch must mint a fresh etag root distinct from step 1's etags.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g2 = assert_ok(r)
    assert g2["node_state"] == "PENDING"
    etag_step2_initial = g2["etag"]
    assert etag_step2_initial != etag_step1_initial
    assert etag_step2_initial != etag_step1_after_patch

    # PATCH with a step-1 etag must 412 (working tree was rebuilt).
    stale = _patch_custom_input(client, run_id, etag_step1_after_patch, "x")
    assert stale.status_code == 412
    assert_error(stale, code="PRECONDITION_FAILED")

    pr = _patch_custom_input(client, run_id, etag_step2_initial, value="beta")
    p2 = assert_ok(pr)
    assert p2["version"] == 1, "version restarts after test_node re-dispatch"
    etag_step2_after_patch = p2["etag"]
    assert etag_step2_after_patch != etag_step2_initial

    # ── Step 3: POST /test/full again ──────────────────────────────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    full2 = assert_ok(r)
    assert full2["run_id"] == run_id
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g3 = assert_ok(r)
    assert g3["node_state"] == "PENDING"
    etag_step3_initial = g3["etag"]
    # Fresh etag distinct from EVERY etag observed in steps 1 and 2.
    assert etag_step3_initial not in {
        etag_step1_initial, etag_step1_after_patch,
        etag_step2_initial, etag_step2_after_patch,
    }, (
        f"step-3 initial etag leaked from a prior step. "
        f"step3={etag_step3_initial!r} "
        f"step1=({etag_step1_initial!r}, {etag_step1_after_patch!r}) "
        f"step2=({etag_step2_initial!r}, {etag_step2_after_patch!r})"
    )

    # Step-2 etag now stale — must 412.
    stale2 = _patch_custom_input(client, run_id, etag_step2_after_patch, "y")
    assert stale2.status_code == 412
    assert_error(stale2, code="PRECONDITION_FAILED")

    pr = _patch_custom_input(client, run_id, etag_step3_initial, value="gamma")
    p3 = assert_ok(pr)
    assert p3["version"] == 1, "version restarts after test/full re-dispatch"
    etag_step3_after_patch = p3["etag"]
    assert etag_step3_after_patch != etag_step3_initial

    # Submit completes the INPUT after the third dispatch — proves the
    # node is fully usable end-to-end after the test/full -> test_node
    # -> test/full sequence.
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/submit")
    submit_body = assert_ok(r)
    assert submit_body["node_state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_get_then_patch_without_drain_matches_live_demo(client, container):
    """Reproduce the HTML demo's exact 1a→1b→1c sequence — no _drain.

    The live HTML demo at docs/design/test_run_data_etag_e2e_live.html
    does POST /test/full → GET /data → PATCH /data with NO orchestration
    drain in between. Users have reported 412 PRECONDITION_FAILED at
    step 1c. This test asserts the GET-returned etag is honored by the
    immediately-following PATCH.
    """
    workflow_id = _create_custom_workflow(client)

    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    run_id = assert_ok(r)["run_id"]
    # NOTE: deliberately no `await _drain(container)` — mirrors the demo.

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g = assert_ok(r)
    etag = g["etag"]

    pr = _patch_custom_input(client, run_id, etag, value="alpha")
    assert pr.status_code == 200, (
        f"GET-then-PATCH (no drain) must succeed; got {pr.status_code} "
        f"body={pr.json()!r}"
    )


@pytest.mark.asyncio
async def test_test_node_after_test_full_completes(client, container):
    """After /test/full reaches COMPLETED, /nodes/{id}/test must re-execute cleanly.

    The full cycle (dispatch → PATCH → submit → COMPLETED) leaves the INPUT
    node in COMPLETED state and the run in RunStatus.COMPLETED. A subsequent
    test_node on the same node must:
      - reuse the workflow's permanent test_run_id,
      - bump generation, clear projector cache, transition the run back to
        a non-terminal status,
      - mint a fresh etag root distinct from any etag observed during the
        prior cycle,
      - reject PATCH with the prior cycle's etag (412),
      - accept PATCH with the fresh etag (version restarts at 1),
      - allow submit to drive the node back to COMPLETED.
    """
    workflow_id = _create_custom_workflow(client)

    # ── Cycle 1: /test/full → PATCH → submit → COMPLETED ────────────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    full = assert_ok(r)
    run_id = full["run_id"]
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g_full = assert_ok(r)
    etag_full_initial = g_full["etag"]

    pr = _patch_custom_input(client, run_id, etag_full_initial, value="alpha")
    p_full = assert_ok(pr)
    etag_full_after_patch = p_full["etag"]

    r = client.post(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/submit")
    assert assert_ok(r)["node_state"] == "COMPLETED"
    await _drain(container)

    # Single-node INPUT-only workflow ⇒ submit drains the run.
    r = client.get(f"/api/v1/runs/{run_id}")
    run_view = assert_ok(r)
    assert run_view["status"] == "COMPLETED", (
        f"after submit, run should be COMPLETED; got {run_view['status']!r}"
    )

    # ── Cycle 2: /nodes/{id}/test on the now-COMPLETED run ──────────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/nodes/{CUSTOM_INPUT_NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    node_res = assert_ok(r)
    assert node_res["run_id"] == run_id, (
        "test_node must reuse the workflow's permanent test_run_id"
    )
    assert node_res["execution_type"] == "pending_input"
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g_node = assert_ok(r)
    assert g_node["node_state"] == "PENDING", (
        f"INPUT must be PENDING after test_node re-dispatch; "
        f"got {g_node['node_state']!r}"
    )
    etag_node_initial = g_node["etag"]
    assert etag_node_initial not in {etag_full_initial, etag_full_after_patch}, (
        "test_node after full-completion must mint a fresh etag root, "
        "distinct from every etag observed in the prior /test/full cycle"
    )

    # Stale etag from the COMPLETED cycle must not satisfy If-Match.
    stale = _patch_custom_input(client, run_id, etag_full_after_patch, "x")
    assert stale.status_code == 412, (
        f"prior-cycle etag must 412; got {stale.status_code} body={stale.json()!r}"
    )
    assert_error(stale, code="PRECONDITION_FAILED")

    # Fresh etag round-trip succeeds; version restarts at 1.
    pr = _patch_custom_input(client, run_id, etag_node_initial, value="delta")
    p_node = assert_ok(pr)
    assert p_node["version"] == 1, (
        "version must restart at 1 after test_node re-dispatch"
    )
    assert p_node["etag"] != etag_node_initial

    # Submit drives the node back to COMPLETED.
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/submit")
    assert assert_ok(r)["node_state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_real_run_input_node_multi_artifact_etag_chain(client, container):
    """End-to-end via POST /api/v1/runs — the production run path.

    Mirrors the HTML demo's step-1 flow (multi-artifact PATCH on a
    custom INPUT form with scalars + nested table) but uses the real
    ``POST /runs`` endpoint instead of ``/test/full``. ``/runs`` enqueues
    a ``StartRunAction`` and returns immediately; the worker then drives
    ``orchestrate_start`` → ``handle_entry_nodes`` async. With the A+D
    fix, the worker runs the INPUT handler INLINE so by the time the
    queue drains, the working tree is fully provisioned and the (single)
    PENDING task carries ``resolved_input``.

    Invariants:
      - ``POST /runs`` mints a fresh run_id (≠ workflow.id).
      - After drain, ``GET /data`` returns ``node_state=PENDING`` with a
        populated ``root`` descriptor and a usable etag.
      - Multi-artifact PATCH (root scalars + 2 table rows in one call)
        commits atomically with ``version=1`` and a fresh etag.
      - Submit + drain ⇒ run reports ``status=COMPLETED``.
    """
    workflow_id = _create_custom_workflow(client)

    r = client.post(
        "/api/v1/runs",
        json={"workflow_id": workflow_id, "input_data": {}},
    )
    started = assert_ok(r)
    run_id = started["id"]
    assert run_id and run_id != workflow_id, (
        f"POST /runs must mint a fresh run_id distinct from workflow_id; "
        f"got {run_id!r}"
    )
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g = assert_ok(r)
    assert g["node_state"] == "PENDING", (
        f"INPUT must be PENDING after /runs dispatch; got {g['node_state']!r}"
    )
    assert g["data"]["root"] is not None, (
        "GET /data must return a populated descriptor tree — proves the "
        "auto-provisioner ran inline within the worker's handle_entry_nodes "
        "(A+D fix), not async after the queue drained"
    )
    etag_initial = g["etag"]
    assert etag_initial.startswith("sha256:")

    pr = client.patch(
        f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data",
        headers={"If-Match": etag_initial},
        json={"artifacts": [
            {
                "artifact_id": "__root__",
                "operations": [{
                    "operation": "insert", "row_id": None,
                    "data": {"input": "real", "input_1": "real-b"},
                }],
            },
            {
                "artifact_id": "table_3_lg2h0",
                "operations": [
                    {"operation": "insert", "row_id": None,
                     "data": {"input_2": "real-r1-A", "input_3": "real-r1-B"}},
                    {"operation": "insert", "row_id": None,
                     "data": {"input_2": "real-r2-A", "input_3": "real-r2-B"}},
                ],
            },
        ]},
    )
    p = assert_ok(pr)
    assert p["version"] == 1, (
        f"version must start at 1 on the first PATCH of a fresh run; got {p['version']!r}"
    )
    assert p["etag"] != etag_initial, "PATCH must mint a new etag"

    r = client.post(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/submit")
    assert assert_ok(r)["node_state"] == "COMPLETED"
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}")
    run_view = assert_ok(r)
    assert run_view["status"] == "COMPLETED", (
        f"after submit + drain, run should be COMPLETED; got {run_view['status']!r}"
    )


@pytest.mark.asyncio
async def test_test_full_after_test_full_completes_can_submit_again(client, container):
    """Reproduces the demo's step-5 bug: /test/full → COMPLETED → /test/full again
    → PATCH → submit must not 409 INVALID_RUN_STATUS.

    The concern: when the prior cycle ended the run as COMPLETED, the
    second /test/full's reset path must transition the run back to a
    non-terminal status visible to the submit-side
    ``read_authoritative_status`` (which reads through the projector view
    store). If the projector still sees COMPLETED at submit time, the
    user gets ``INVALID_RUN_STATUS current_status=COMPLETED``.
    """
    workflow_id = _create_custom_workflow(client)

    # ── Cycle 1: full /test/full → submit → COMPLETED ─────────────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    run_id = assert_ok(r)["run_id"]
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    etag1 = assert_ok(r)["etag"]
    assert assert_ok(_patch_custom_input(client, run_id, etag1, "alpha"))
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/submit")
    assert assert_ok(r)["node_state"] == "COMPLETED"
    await _drain(container)

    r = client.get(f"/api/v1/runs/{run_id}")
    assert assert_ok(r)["status"] == "COMPLETED"

    # ── Cycle 2: /test/full from COMPLETED → PATCH → submit ───────────
    r = client.post(
        f"/api/v1/workflows/{workflow_id}/test/full",
        json={"input_data": {}},
    )
    assert assert_ok(r)["run_id"] == run_id
    await _drain(container)

    # After the reset, the projected run status must NOT be COMPLETED —
    # otherwise submit will reject with INVALID_RUN_STATUS.
    r = client.get(f"/api/v1/runs/{run_id}")
    status_before_patch = assert_ok(r)["status"]
    assert status_before_patch in ("RUNNING", "PENDING"), (
        f"after restart from COMPLETED, projected run status must be non-"
        f"terminal; got {status_before_patch!r} — submit will 409 if this "
        f"isn't reset"
    )

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/data")
    g2 = assert_ok(r)
    etag2 = g2["etag"]
    assert g2["node_state"] == "PENDING"

    pr = _patch_custom_input(client, run_id, etag2, "delta")
    p2 = assert_ok(pr)
    assert p2["version"] == 1

    # The bug: this submit returns 409 INVALID_RUN_STATUS in production
    # because the projector still reports COMPLETED.
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{CUSTOM_INPUT_NODE_ID}/submit")
    submit_body = assert_ok(r)
    assert submit_body["node_state"] == "COMPLETED", (
        f"submit must succeed after restart-from-COMPLETED; got {r.json()!r}"
    )
