"""Re-running a test workflow must fully clear prior-execution state.

Reported symptom: after an INPUT node was completed in test run #1, a
subsequent test run #2 (same workflow, same test_run_id) shows the node
as ``COMPLETED`` — stale from the previous execution — instead of
``PENDING`` after the fresh dispatch.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.value_objects import NodeType
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok
from tests.helpers.queue_runner import QueueRunner


INPUT_SCHEMA = [
    {"key": "input", "outputType": "string",
     "fieldConfig": {"fieldWrapper": "FormItemWrapper"}},
]

WORKFLOW_ID = "wf-test-reset-1"
NODE_ID = "n_input"


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
    wf = Workflow(
        id=WORKFLOW_ID,
        name="Reset probe",
        nodes=[
            WorkflowNode(
                id=NODE_ID, name="Input", node_type=NodeType.INPUT,
                config={"input_schema": INPUT_SCHEMA},
            ),
        ],
        edges=[],
    )
    container["get_node_schema"].workflow_repo.save(wf)


@pytest.mark.asyncio
async def test_test_run_reset_fully_clears_completed_node_state(client, container):
    _seed_workflow(container)

    # --- Test run #1 — let the INPUT node complete. ------------------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/test/full",
        json={"input_data": {}},
    )
    first = assert_ok(r)
    run_id = first["run_id"]
    await _drain(container)

    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    submit_body = assert_ok(r)
    assert submit_body["node_state"] == "COMPLETED"
    await _drain(container)

    # Sanity: the node really is COMPLETED at the end of run #1.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    data = assert_ok(r)
    assert data["node_state"] == "COMPLETED"

    # Record the first run's aggregate state for a direct projector check
    # after the reset.
    first_view = container["run_context_view_store"].get(run_id)
    assert NODE_ID in first_view.completed_nodes

    # --- Test run #2 — re-run the same workflow. --------------------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/test/full",
        json={"input_data": {}},
    )
    second = assert_ok(r)
    assert second["run_id"] == run_id, (
        "test-full-workflow is permanent 1:1 with the workflow — "
        "re-run should reuse the same run_id"
    )

    # Drain the new dispatch so a fresh PENDING task lands.
    await _drain(container)

    # After reset + fresh dispatch: node state must be PENDING, not COMPLETED.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    data = assert_ok(r)
    assert data["node_state"] == "PENDING", (
        f"expected PENDING after test-run reset; got {data['node_state']!r}. "
        f"State from the previous run leaked through."
    )

    # The view must reflect the cleared state too.
    view = container["run_context_view_store"].get(run_id)
    assert NODE_ID not in view.completed_nodes, (
        f"completed_nodes should be empty after reset; got {view.completed_nodes!r}"
    )
    assert NODE_ID not in view.skipped_nodes
    # The prior run's post-submit ``output_port == "success"`` must not
    # leak through. After the reset the new dispatch publishes a fresh
    # ``NodeOutputProduced`` so the port is either absent (no re-emission
    # yet) or the new PENDING-dispatch value — never the stale "success".
    leaked = (view.last_output_ports or {}).get(NODE_ID)
    assert leaked != "success", (
        f"last_output_ports carried the previous run's 'success' port "
        f"through the reset; got {view.last_output_ports!r}"
    )


@pytest.mark.asyncio
async def test_test_full_rerun_creates_fresh_task_and_working_ref(client, container):
    """Re-running test-full on a workflow whose test run already completed
    must produce a fresh PENDING task AND a fresh working copy for the node.

    Reported symptom: after a completed test run, re-triggering test-full
    returns the old COMPLETED task and GET /data shows the previously
    submitted values — the user can't enter new input.

    The working copy that carries user-PATCHed values lives in
    ``view.variables[node_id]``; ``task.resolved_input`` stays frozen at
    the initial (header-only) provision from dispatch. Both must be
    replaced with fresh file_ids on re-run.
    """
    _seed_workflow(container)

    # --- Run #1: dispatch → PATCH user input → submit. ---------------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/test/full",
        json={"input_data": {}},
    )
    first = assert_ok(r)
    run_id = first["run_id"]
    await _drain(container)

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    task_repo = container["get_node_schema"].task_repo
    fs = container["file_service_client"]

    # Record the run-1 dispatch task's resolved_input (header-only CSV).
    first_task = task_repo.get_by_id(f"{run_id}-{NODE_ID}")
    assert first_task is not None
    first_input_file_id = first_task.resolved_input["file_id"]
    first_task_created_at = first_task.created_at

    # PATCH the working copy with user input.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    etag_1 = assert_ok(r)["etag"]
    patch_result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_1,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "PREVIOUS-RUN-DATA"},
            }],
        }],
    ))
    assert patch_result.error is None

    # The PATCH created a NEW file_id for the working root CSV.
    view_after_patch = container["run_context_view_store"].get(run_id)
    first_working_file_id = view_after_patch.variables[NODE_ID]["file_id"]
    assert first_working_file_id != first_input_file_id
    body_working = (await fs.download_file(first_working_file_id)) or b""
    assert b"PREVIOUS-RUN-DATA" in body_working

    # Submit → node COMPLETED.
    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    first_task_after_submit = task_repo.get_by_id(f"{run_id}-{NODE_ID}")
    assert first_task_after_submit.status.value == "COMPLETED"

    # --- Run #2: re-trigger test-full on the same workflow. ----------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/test/full",
        json={"input_data": {}},
    )
    second = assert_ok(r)
    assert second["run_id"] == run_id  # same permanent test_run_id
    await _drain(container)

    # 1. Task replaced (same id, fresh instance, PENDING status, new created_at).
    second_task = task_repo.get_by_id(f"{run_id}-{NODE_ID}")
    assert second_task is not None
    assert second_task.status.value == "PENDING", (
        f"task should be PENDING after re-dispatch; got {second_task.status!r}"
    )
    assert second_task.created_at > first_task_created_at, (
        f"task created_at did not advance — new dispatch reused the prior "
        f"Task instance. before={first_task_created_at!r} "
        f"after={second_task.created_at!r}"
    )

    # 2. task.resolved_input must point at a NEW header-only CSV.
    second_input_file_id = second_task.resolved_input["file_id"]
    assert second_input_file_id
    assert second_input_file_id != first_input_file_id, (
        "re-dispatch reused the prior run's task.resolved_input file_id"
    )
    body_input = (await fs.download_file(second_input_file_id)) or b""
    assert b"PREVIOUS-RUN-DATA" not in body_input, (
        "new task.resolved_input points at a CSV that still contains the "
        "previous run's submitted data"
    )

    # 3. view.variables[node_id] must be fresh too — NOT the file that
    # carried "PREVIOUS-RUN-DATA" from the last run.
    view_2 = container["run_context_view_store"].get(run_id)
    second_working_file_id = view_2.variables[NODE_ID]["file_id"]
    assert second_working_file_id != first_working_file_id, (
        "view.variables[node_id] still points at the previous run's working "
        "copy. GET /data?side=output will return the old submitted data."
    )
    body_working_2 = (await fs.download_file(second_working_file_id)) or b""
    assert b"PREVIOUS-RUN-DATA" not in body_working_2, (
        "view.variables[node_id] working file still contains the previous "
        "run's submitted data"
    )

    # 4. GET /data must reflect the fresh state end-to-end.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=both")
    data = assert_ok(r)
    assert data["node_state"] == "PENDING"
    assert data["task_id"] == f"{run_id}-{NODE_ID}"
    assert data["data"]["root"]["file_id"] == second_input_file_id
    assert data["output"]["root"]["file_id"] == second_working_file_id


@pytest.mark.asyncio
async def test_test_node_rerun_clears_node_from_projected_completed(
    client, container,
):
    """``test_node`` re-run must not surface COMPLETED from the previous
    execution. Today the use case discards the node from the AGGREGATE
    ``completed_nodes`` set but doesn't clear the event log or invalidate
    the projector cache — so the projector replays the prior
    ``NodeCompleted`` event and ``GET /data`` reports COMPLETED even
    after the new dispatch made the task PENDING.
    """
    _seed_workflow(container)

    # --- Run #1: test_node → PENDING → submit → COMPLETED. ---------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    first = assert_ok(r)
    run_id = first["run_id"]
    assert first["execution_type"] == "pending_input"

    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    view_after_submit = container["run_context_view_store"].get(run_id)
    assert NODE_ID in view_after_submit.completed_nodes

    # --- Run #2: test_node again on the same node. ------------------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    second = assert_ok(r)
    assert second["execution_type"] == "pending_input"

    # The projected view must no longer report the node as COMPLETED.
    view_after_rerun = container["run_context_view_store"].get(run_id)
    assert NODE_ID not in view_after_rerun.completed_nodes, (
        f"projected view still reports {NODE_ID!r} as COMPLETED after "
        f"test_node re-run; event log was not cleared. "
        f"completed_nodes={view_after_rerun.completed_nodes!r}"
    )

    # And GET /data must reflect the fresh PENDING state.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    data = assert_ok(r)
    assert data["node_state"] == "PENDING", (
        f"node_state should be PENDING after test_node re-run; "
        f"got {data['node_state']!r}"
    )


@pytest.mark.asyncio
async def test_test_node_rerun_then_patch_output_reflects_latest_edit(
    client, container,
):
    """Full re-edit cycle on test_node:

    Run #1: test_node → PATCH "OLD" → submit (COMPLETED).
    Run #2: test_node again → PATCH "NEW".
    Expectation: ``GET /data?side=output`` after run-2's PATCH must
    surface "NEW", not "OLD". Regression for the user-reported
    "output didn't change after re-test" symptom — covers the chain
    where a stale task / stale event-log envelope misroutes the PATCH.
    """
    _seed_workflow(container)

    # --- Run #1 ---
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    first = assert_ok(r)
    run_id = first["run_id"]

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    fs = container["file_service_client"]

    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    etag_1 = assert_ok(r)["etag"]
    res = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_1,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "OLD"},
            }],
        }],
    ))
    assert res.error is None

    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    # --- Run #2: re-dispatch via test_node ---
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    second = assert_ok(r)
    assert second["execution_type"] == "pending_input"

    # PATCH the freshly-dispatched working copy with "NEW".
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=output")
    output_payload = assert_ok(r)
    etag_2 = output_payload["etag"]
    pre_patch_file_id = output_payload["output"]["root"]["file_id"]

    res2 = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_2,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "NEW"},
            }],
        }],
    ))
    assert res2.error is None, (
        f"PATCH after test_node re-run failed: {res2.error_code}: {res2.error}"
    )
    # The PATCH must produce a file_id different from what the view
    # was holding before the PATCH.
    post_patch_file_id = res2.file_lineage.get(pre_patch_file_id) or ""
    assert post_patch_file_id and post_patch_file_id != pre_patch_file_id, (
        f"PATCH didn't produce a new file_id; lineage={res2.file_lineage!r}"
    )

    # Read back: GET /data?side=output must return the post-PATCH file_id
    # AND the CSV must contain "NEW", not "OLD".
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=output")
    final = assert_ok(r)
    output_root = final["output"]["root"]
    assert output_root is not None
    assert output_root["file_id"] == post_patch_file_id, (
        f"GET /data?side=output didn't pick up the PATCH; "
        f"expected {post_patch_file_id!r}, got {output_root['file_id']!r}"
    )

    body = (await fs.download_file(output_root["file_id"])) or b""
    assert b"NEW" in body, (
        f"working CSV doesn't contain the latest PATCH; body={body!r}"
    )
    assert b"OLD" not in body, (
        f"working CSV still carries pre-rerun submission; body={body!r}"
    )

    # _resolve_task must have picked up the fresh (re-dispatched) task,
    # not the stale one from run #1. Task id is deterministic, so both
    # runs share the same id — but the fresh Task row has an advanced
    # ``created_at`` from the most recent dispatch, and the task's
    # ``status`` should be PENDING (not COMPLETED from run #1).
    task_repo = container["get_node_schema"].task_repo
    task = task_repo.get_by_id(f"{run_id}-{NODE_ID}")
    assert task is not None
    assert task.status.value == "PENDING", (
        f"PATCH ran against a stale task; status={task.status!r}"
    )


@pytest.mark.asyncio
async def test_test_node_builtin_output_visible_in_view_after_dispatch(
    client, container,
):
    """The BUILTIN branch of ``test_node`` must publish its
    ``state_updates`` as ``NodeOutputProduced`` so the projector folds
    them into ``view.variables[node_id]``. Otherwise downstream consumers
    of the run-context view (GET /data, variable resolver via the view
    store, dispatcher event-driven paths) see an empty slot for a node
    that just ran successfully — same shape of bug as the PENDING
    branch had.
    """
    from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
    from app.layer1_domain.value_objects import NodeType

    # CONDITION emits NodeDispatched / NodeCompleted, so the event log is
    # non-empty after the inline test_node call — which disables the
    # view-store's aggregate-fallback and forces the projector to decide
    # state. Neither event rebuilds ``variables``, so unless the BUILTIN
    # branch also emits ``NodeOutputProduced`` via
    # ``apply_state_updates_as_files``, ``view.variables[node_id]`` stays
    # empty despite the handler having produced output.
    wf = Workflow(
        id="wf-builtin-out", name="builtin-output",
        nodes=[
            WorkflowNode(
                id="n_cond", name="COND", node_type=NodeType.CONDITION,
                config={"expression": "true"},
            ),
            WorkflowNode(
                id="n_true", name="T", node_type=NodeType.TRIGGER, config={},
            ),
            WorkflowNode(
                id="n_false", name="F", node_type=NodeType.TRIGGER, config={},
            ),
        ],
        edges=[
            WorkflowEdge(
                id="e1", source_node_id="n_cond",
                target_node_id="n_true", condition="true",
            ),
            WorkflowEdge(
                id="e2", source_node_id="n_cond",
                target_node_id="n_false", condition="false",
            ),
        ],
    )
    container["get_node_schema"].workflow_repo.save(wf)

    r = client.post(
        f"/api/v1/workflows/wf-builtin-out/nodes/n_cond/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    res = assert_ok(r)
    run_id = res["run_id"]
    assert res["execution_type"] == "inline"

    view = container["run_context_view_store"].get(run_id)
    assert view is not None
    assert "n_cond" in view.variables, (
        "BUILTIN test_node didn't propagate state_updates to the view; "
        "apply_state_updates (aggregate-only) was used instead of "
        "apply_state_updates_as_files (event-emitting)."
    )


@pytest.mark.asyncio
async def test_test_node_rerun_output_side_refreshed_and_etag_matches_input(
    client, container,
):
    """After test_node re-dispatch the output side must reflect the freshly
    provisioned file_ref — not the previous run's working copy — and the
    input/output etags must match because both sides point at the same
    freshly-provisioned tree.

    Task id is deterministic (``{run_id}-{node_id}``), so re-test
    overwrites the same row. Upon re-dispatch, ``task.resolved_input``
    and ``view.variables[node_id]`` both reference the new provisioned
    ref; etag parity is the cheapest invariant to assert that the state
    was genuinely reset on both sides.
    """
    _seed_workflow(container)

    # Run #1 — dispatch, PATCH the working copy, submit → COMPLETED.
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    first = assert_ok(r)
    run_id = first["run_id"]

    from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
        EditNodeDataCommand,
    )
    uc = container["edit_node_data"]
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data")
    etag_pre_patch = assert_ok(r)["etag"]
    patch_result = await uc.execute(EditNodeDataCommand(
        run_id=run_id, node_id=NODE_ID, if_match=etag_pre_patch,
        artifacts=[{
            "artifact_id": "__root__",
            "operations": [{
                "operation": "insert", "row_id": None,
                "data": {"input": "STALE-RUN-1"},
            }],
        }],
    ))
    assert patch_result.error is None

    r = client.post(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/submit")
    assert_ok(r)
    await _drain(container)

    # --- Run #2 — test_node on the same node. -------------------------
    r = client.post(
        f"/api/v1/workflows/{WORKFLOW_ID}/nodes/{NODE_ID}/test",
        json={"input_data": {}, "mock_variables": {}},
    )
    second = assert_ok(r)
    assert second["execution_type"] == "pending_input"

    # Output side must NOT still carry run-1's working copy.
    r = client.get(f"/api/v1/runs/{run_id}/nodes/{NODE_ID}/data?side=both")
    data = assert_ok(r)

    assert data["node_state"] == "PENDING"
    assert data["data"] is not None
    assert data["output"] is not None
    assert data["data"]["root"] is not None, (
        "side=input must surface the freshly provisioned root ref"
    )
    assert data["output"]["root"] is not None, (
        "side=output still empty/stale — handler_result.state_updates "
        "wasn't applied to the aggregate on test_node re-dispatch"
    )

    input_file_id = data["data"]["root"]["file_id"]
    output_file_id = data["output"]["root"]["file_id"]
    assert input_file_id == output_file_id, (
        f"input/output root file_ids must match on a freshly re-dispatched "
        f"pending node; got input={input_file_id!r} output={output_file_id!r}"
    )

    # The file content must not contain the previous run's submission.
    fs = container["file_service_client"]
    body = (await fs.download_file(output_file_id)) or b""
    assert b"STALE-RUN-1" not in body, (
        "output side still references run-1's edited working file"
    )

    # ETag parity — the most direct evidence that both sides point at
    # the same freshly provisioned tree.
    assert data["data"]["etag"] == data["output"]["etag"], (
        f"input/output etags diverged after test_node re-dispatch; "
        f"input={data['data']['etag']!r} output={data['output']['etag']!r}"
    )
