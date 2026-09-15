"""Integration tests for the SA-1372 replay-debugger HTTP endpoints (TestClient).

Exercises the four routes end-to-end through the real app + DI container +
response envelope:

- POST   /api/v1/runs/{id}/replay
- POST   /api/v1/runs/{id}/replay/{sid}/step
- GET    /api/v1/runs/{id}/replay/{sid}/state
- DELETE /api/v1/runs/{id}/replay/{sid}

The run's event log is seeded directly on the container's event_log_repo (the
same instance the replay use case reads), so no live workflow execution is
needed. Authority: 65537.
"""
import pytest
from fastapi.testclient import TestClient

from app.layer1_domain.event_log import EventLogEnvelope
from app.layer3_adapters.controllers.restful.v1 import create_fastapi_app
from bootstrap import build_app_container
from tests.conftest import assert_ok, assert_error

RUN = "run-int-1"


@pytest.fixture
def container():
    return build_app_container()


@pytest.fixture
def client(container):
    return TestClient(create_fastapi_app(container))


def _seed(container):
    repo = container["event_log_repo"]
    events = [
        ("RunStatusChanged", {"status": "started", "run_id": RUN, "workflow_id": "wf-1", "input_data": {}}),
        ("NodeInput", {"node_id": "n1", "resolved_input": {"x": 1, "password": "hunter2"}}),
        ("NodeOutputProduced", {
            "node_id": "n1",
            "state_updates": {"score": 42, "api_key": "sk-live-xyz"},
            "output_port": "success", "taken_edge_ports": {"e1": "success"},
            "__credentials": {"token": "secret-token"},
        }),
        ("NodeCompleted", {"node_id": "n1"}),
        ("RunStatusChanged", {"status": "completed", "output_data": {"score": 42}}),
    ]
    for i, (etype, payload) in enumerate(events, start=1):
        repo.append(EventLogEnvelope(
            event_id=f"e{i}", run_id=RUN, topic="t", event_type=etype,
            timestamp=f"2026-07-01T00:00:0{i}+00:00", payload=payload,
        ))


def test_replay_lifecycle_and_redaction(client, container):
    _seed(container)

    # open
    data = assert_ok(client.post(f"/api/v1/runs/{RUN}/replay"))
    assert data["total_steps"] == 5 and data["cursor"] == 0
    assert len(data["timeline"]) == 5
    sid = data["replay_session_id"]

    # step to the output event -> secrets redacted end-to-end
    st = assert_ok(client.post(f"/api/v1/runs/{RUN}/replay/{sid}/step", json={"op": "index", "index": 3}))
    assert st["node_id"] == "n1"
    assert st["node_io"]["state_updates"]["api_key"] == "***REDACTED***"
    assert st["event"]["payload"]["__credentials"] == "***REDACTED***"
    assert st["scope_chain"]["score"] == 42
    assert "api_key" in st["redacted_keys"]
    assert "n1" in st["node_status"]  # canvas-overlay status present over HTTP

    # run_to node lands on n1
    st = assert_ok(client.post(f"/api/v1/runs/{RUN}/replay/{sid}/step", json={"op": "run_to", "node_id": "n1"}))
    assert st["node_id"] == "n1"

    # GET state
    st = assert_ok(client.get(f"/api/v1/runs/{RUN}/replay/{sid}/state"))
    assert st["run_id"] == RUN

    # non-mutating: source log untouched
    assert len(container["event_log_repo"].list_by_run(RUN)) == 5

    # close
    assert assert_ok(client.delete(f"/api/v1/runs/{RUN}/replay/{sid}"))["closed"] is True


def test_replay_run_not_found(client):
    assert_error(client.post("/api/v1/runs/nope-xyz/replay"), "RUN_NOT_FOUND")


def test_replay_timeline_paged_over_http(client, container):
    """Server-side paging: open ships only the first page; further pages come
    from GET .../timeline with limit/offset + total (execution-log convention)."""
    _seed(container)

    data = assert_ok(client.post(f"/api/v1/runs/{RUN}/replay", params={"timeline_limit": 2}))
    sid = data["replay_session_id"]
    assert data["total_steps"] == 5
    assert len(data["timeline"]) == 2  # first page only, not the whole log
    assert [t["index"] for t in data["timeline"]] == [1, 2]

    page2 = assert_ok(client.get(
        f"/api/v1/runs/{RUN}/replay/{sid}/timeline",
        params={"offset": 2, "limit": 2},
    ))
    assert page2["total"] == 5 and page2["offset"] == 2 and page2["limit"] == 2
    assert [t["index"] for t in page2["items"]] == [3, 4]

    tail = assert_ok(client.get(
        f"/api/v1/runs/{RUN}/replay/{sid}/timeline",
        params={"offset": 4, "limit": 2},
    ))
    assert [t["index"] for t in tail["items"]] == [5]

    # stepping still works against the paged session
    st = assert_ok(client.post(f"/api/v1/runs/{RUN}/replay/{sid}/step", json={"op": "index", "index": 5}))
    assert st["replay_complete"] is True

    assert_error(
        client.get(f"/api/v1/runs/{RUN}/replay/bogus/timeline"),
    )
