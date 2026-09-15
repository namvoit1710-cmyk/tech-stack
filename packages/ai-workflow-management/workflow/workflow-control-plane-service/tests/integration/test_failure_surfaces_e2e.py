"""End-to-end: a failed worker task must surface status=FAILED + the error
body across every read surface.

Drives the failure through the real ``fail_task`` use case (so failure_info
is written via NodeOutputProduced exactly as in production), then asserts
each HTTP read surface. The error body — the worker's HTTP response — is
file-backed (via the data store), so "body present" means the failure_info
file-ref / its columns are reachable, not inline JSON.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import (
    FailTaskCommand,
)
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok

WF = "wf-fail"
RUN = "run-fail"
RESPONSE_BODY = {"detail": "downstream exploded"}

WF_H = "wf-hfail"
RUN_H = "run-hfail"


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


async def _fail_a_task(container):
    uc = container["get_node_execution"]
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK, worker_type="w1")
    uc.workflow_repo.save(Workflow(id=WF, name="Fail", nodes=[node], edges=[]))
    uc.run_repo.save(WorkflowRun(id=RUN, workflow_id=WF, status=RunStatus.RUNNING))
    task = Task(run_id=RUN, node_id="task-1", status=TaskStatus.RUNNING, worker_type="w1")
    uc.task_repo.save(task)

    await container["fail_task"].execute(FailTaskCommand(
        run_id=RUN,
        task_id=task.id,
        error={"code": "HTTP_500", "message": "HTTP 500"},
        outputs={"status_code": 500, "response_body": RESPONSE_BODY, "is_success": False},
    ))
    return task.id


@pytest.mark.asyncio
async def test_node_output_panel_surfaces_failure_with_body(client, container):
    await _fail_a_task(container)
    data = assert_ok(client.get(f"/api/v1/runs/{RUN}/nodes/task-1/output"))
    assert len(data["iterations"]) == 1
    it = data["iterations"][0]
    assert it["status"] == "FAILED"
    assert it["error"] == "HTTP 500"
    assert it["error_code"] == "HTTP_500"
    # Body present: the failure_info surfaced as a scope (previously [] —
    # failed tasks were collapsed to status-only).
    assert it["scopes"], "expected the failure body to surface as a scope"


@pytest.mark.asyncio
async def test_node_execution_surfaces_failure_with_body(client, container):
    await _fail_a_task(container)
    data = assert_ok(client.get(f"/api/v1/runs/{RUN}/nodes/task-1/execution"))
    assert data["status"] == "FAILED"
    ex = data["executions"][0]
    assert ex["status"] == "FAILED"
    assert ex["error"] == "HTTP 500"
    assert ex["error_code"] == "HTTP_500"
    # context_output now sourced from the view store (aggregate is stale) —
    # the failure_info file-ref carrying the body.
    assert data["context_output"] is not None, "expected the failure output to surface"


@pytest.mark.asyncio
async def test_get_data_reports_failed_state_and_output_body(client, container):
    await _fail_a_task(container)
    # node_state reflects the failure regardless of side (was "COMPLETED").
    default = assert_ok(client.get(f"/api/v1/runs/{RUN}/nodes/task-1/data"))
    assert default["node_state"] == "FAILED"
    # node_error surfaces uniformly (handler AND worker failures).
    assert default["node_error"] is not None
    assert default["node_error"]["error_code"] == "HTTP_500"
    # The body lives on the output side (the working copy / failure_info).
    out = assert_ok(client.get(f"/api/v1/runs/{RUN}/nodes/task-1/data", params={"side": "output"}))
    assert out["node_state"] == "FAILED"
    assert out["output"] is not None and out["output"]["root"] is not None, \
        "expected the failure body file-ref on the output side"


@pytest.mark.asyncio
async def test_execution_log_records_failure_and_body(client, container):
    await _fail_a_task(container)
    data = assert_ok(client.get(f"/api/v1/runs/{RUN}/execution-log"))
    assert data["summary"]["node_counts"]["failed"] == 1
    by_type = {e["event_type"]: e for e in data["events"]}
    assert "NodeFailed" in by_type
    assert by_type["NodeFailed"]["payload"]["error_message"] == "HTTP 500"
    assert by_type["NodeFailed"]["payload"]["error_code"] == "HTTP_500"
    # The body is carried by the failure-port NodeOutputProduced event.
    assert "NodeOutputProduced" in by_type
    state_updates = by_type["NodeOutputProduced"]["payload"]["state_updates"]
    assert "task-1" in state_updates


# ---------------------------------------------------------------------------
# In-engine HANDLER / expression failures. Unlike worker failures these write
# NO failure_info to the node output (doing so would self-route via the
# DispatcherConsumer), so the error must still surface read-side: node_state,
# the error message, and the error code.
# ---------------------------------------------------------------------------

def _seed_handler_failure(container):
    uc = container["get_node_execution"]
    node = WorkflowNode(id="cond-1", name="Branch", node_type=NodeType.CONDITION)
    uc.workflow_repo.save(Workflow(id=WF_H, name="HFail", nodes=[node], edges=[]))
    uc.run_repo.save(WorkflowRun(id=RUN_H, workflow_id=WF_H, status=RunStatus.RUNNING))
    task = Task(
        run_id=RUN_H, node_id="cond-1", status=TaskStatus.FAILED,
        error="Expression error: $.missing is undefined",
        output_metadata={"error_code": "ExpressionError"},
        completed_at="2026-05-30T00:00:00Z",
    )
    uc.task_repo.save(task)
    # RunStatusChanged so the projected view carries workflow_id/status, then
    # NodeFailed (no NodeOutputProduced) — what _mark_task_failed emits.
    container["event_log_repo"].append(EventLogEnvelope(
        event_id="rs1", run_id=RUN_H, topic="run.status", event_type="RunStatusChanged",
        timestamp="2026-05-30T00:00:00Z",
        payload={"run_id": RUN_H, "workflow_id": WF_H, "status": "started"},
    ))
    container["event_log_repo"].append(EventLogEnvelope(
        event_id="hf1", run_id=RUN_H, topic="node.failed", event_type="NodeFailed",
        timestamp="2026-05-30T00:00:01Z",
        payload={
            "node_id": "cond-1", "task_id": task.id,
            "run_id": RUN_H, "workflow_id": WF_H,
            "error_code": "ExpressionError",
            "error_message": "Expression error: $.missing is undefined",
        },
    ))
    return task.id


def test_handler_failure_surfaces_on_get_data(client, container):
    _seed_handler_failure(container)
    # node_state reflects the failure, and node_error carries the reason even
    # though there is no output ref (handler errors write no failure_info).
    for side in ("input", "output"):
        data = assert_ok(client.get(
            f"/api/v1/runs/{RUN_H}/nodes/cond-1/data", params={"side": side},
        ))
        assert data["node_state"] == "FAILED"
        assert data["node_error"] is not None
        assert "Expression error" in data["node_error"]["message"]
        assert data["node_error"]["error_code"] == "ExpressionError"


def test_handler_failure_surfaces_on_output_and_execution(client, container):
    _seed_handler_failure(container)
    out = assert_ok(client.get(f"/api/v1/runs/{RUN_H}/nodes/cond-1/output"))
    it = out["iterations"][0]
    assert it["status"] == "FAILED"
    assert "Expression error" in it["error"]
    assert it["error_code"] == "ExpressionError"
    # No rich body for a handler error → no scopes, and that's correct.
    assert it["scopes"] == []

    ex = assert_ok(client.get(f"/api/v1/runs/{RUN_H}/nodes/cond-1/execution"))
    assert ex["status"] == "FAILED"
    assert ex["executions"][0]["error_code"] == "ExpressionError"
    assert "Expression error" in ex["executions"][0]["error"]


def test_eventless_failure_still_surfaces_node_error(client, container):
    # The hardest shape: a FAILED task with NO node-level event (e.g. the CSV
    # data-mode silent failure, which marks the task FAILED but emits no
    # NodeFailed). node_error keys off task.status, not events, so it must
    # still surface the reason at GET /data.
    uc = container["get_node_execution"]
    node = WorkflowNode(id="task-9", name="CsvNode", node_type=NodeType.TASK, worker_type="w1")
    uc.workflow_repo.save(Workflow(id="wf-ev", name="Ev", nodes=[node], edges=[]))
    uc.run_repo.save(WorkflowRun(id="run-ev", workflow_id="wf-ev", status=RunStatus.RUNNING))
    task = Task(
        run_id="run-ev", node_id="task-9", status=TaskStatus.FAILED,
        error="CSV converter or file service not configured",
        completed_at="2026-05-30T00:00:00Z",
    )
    uc.task_repo.save(task)
    # Only a run-level event — NO node event for task-9.
    container["event_log_repo"].append(EventLogEnvelope(
        event_id="ev-rs", run_id="run-ev", topic="run.status", event_type="RunStatusChanged",
        timestamp="2026-05-30T00:00:00Z",
        payload={"run_id": "run-ev", "workflow_id": "wf-ev", "status": "started"},
    ))

    data = assert_ok(client.get("/api/v1/runs/run-ev/nodes/task-9/data", params={"side": "output"}))
    assert data["node_state"] == "FAILED"
    assert data["node_error"] is not None
    assert "CSV converter" in data["node_error"]["message"]


@pytest.mark.asyncio
async def test_mark_task_failed_stashes_error_code(container):
    # The shared handler-error helper now mirrors the code into
    # output_metadata so the read APIs can surface it.
    engine = container["fail_task"]._engine
    node = WorkflowNode(id="cond-9", name="B", node_type=NodeType.CONDITION)
    run = WorkflowRun(id="run-mk", workflow_id="wf-mk", status=RunStatus.RUNNING)
    container["get_node_execution"].run_repo.save(run)
    task = Task(run_id="run-mk", node_id="cond-9", status=TaskStatus.RUNNING)
    container["get_node_execution"].task_repo.save(task)

    await engine._mark_task_failed(task, node, run, "Expression error: boom")

    assert task.status == TaskStatus.FAILED
    assert task.error == "Expression error: boom"
    assert (task.output_metadata or {}).get("error_code") == "ExpressionError"
