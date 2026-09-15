"""Integration test for GET /runs/{run_id}/execution-log.

Exercises the full HTTP path through FastAPI — request → response-wrapper
middleware → route → Depends → use case → JSON — and confirms the typed
query params (``event_type``, ``since_sequence: int``, ``limit``/``offset``,
``include_children: bool``) parse from the query string.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import RunStatus
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok

WORKFLOW_ID = "wf-el-1"
RUN_ID = "run-el-1"


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _emit(container, event_type, event_id, *, ts, payload):
    container["event_log_repo"].append(EventLogEnvelope(
        event_id=event_id, run_id=RUN_ID, topic="t",
        event_type=event_type, timestamp=ts, payload=payload,
    ))


def _seed(container):
    uc = container["get_node_output"]  # any use case exposing the repos
    uc.workflow_repo.save(Workflow(id=WORKFLOW_ID, name="EL Test", nodes=[], edges=[]))
    uc.run_repo.save(WorkflowRun(id=RUN_ID, workflow_id=WORKFLOW_ID, status=RunStatus.RUNNING))
    _emit(container, "RunStatusChanged", "e1", ts="2026-05-29T00:00:01Z", payload={"status": "started"})
    _emit(container, "NodeDispatched", "e2", ts="2026-05-29T00:00:02Z", payload={"node_id": "a", "task_id": "t1"})
    _emit(container, "NodeCompleted", "e3", ts="2026-05-29T00:00:03Z", payload={"node_id": "a"})
    _emit(container, "NodeFailed", "e4", ts="2026-05-29T00:00:04Z", payload={"node_id": "b"})


def test_execution_log_endpoint_wraps_and_parses_params(client, container):
    _seed(container)

    # Full log — response-wrapper envelope + ordered events + summary.
    data = assert_ok(client.get(f"/api/v1/runs/{RUN_ID}/execution-log"))
    assert isinstance(data["summary"]["status"], str) and data["summary"]["status"]
    assert data["summary"]["total_events"] == 4
    assert data["summary"]["node_counts"] == {"completed": 1, "failed": 1, "skipped": 0}
    assert [e["event_type"] for e in data["events"]] == [
        "RunStatusChanged", "NodeDispatched", "NodeCompleted", "NodeFailed",
    ]
    assert data["total"] == 4

    # event_type filter parses from the query string.
    data2 = assert_ok(client.get(
        f"/api/v1/runs/{RUN_ID}/execution-log", params={"event_type": "NodeFailed"},
    ))
    assert [e["event_type"] for e in data2["events"]] == ["NodeFailed"]

    # since_sequence (int) + pagination (int) parse and apply.
    data3 = assert_ok(client.get(
        f"/api/v1/runs/{RUN_ID}/execution-log",
        params={"since_sequence": 2, "limit": 1, "offset": 0},
    ))
    assert all(e["sequence"] > 2 for e in data3["events"])
    assert len(data3["events"]) == 1
    assert data3["total"] == 2

    # include_children (bool) parses from the string "false".
    data4 = assert_ok(client.get(
        f"/api/v1/runs/{RUN_ID}/execution-log", params={"include_children": "false"},
    ))
    assert data4["summary"]["run_ids"] == [RUN_ID]


def test_execution_log_run_not_found(client):
    resp = client.get("/api/v1/runs/missing-run/execution-log")
    assert resp.status_code == 404
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "RUN_NOT_FOUND"
