import asyncio

import httpx

import app.layer4_frameworks.clients.workflow_clients as wc
from app.layer4_frameworks.clients.workflow_clients import WorkflowClient


def test_get_execution_log_returns_mock_only_when_opted_in():
    client = WorkflowClient(use_mock=True)

    result = asyncio.run(client.get_execution_log("run-42"))

    assert result["run_id"] == "run-42"
    assert result["summary"]["node_counts"]["failed"] == 1
    assert any(e["event_type"] == "NodeFailed" for e in result["events"])


def test_get_execution_log_is_empty_when_unconfigured_without_mock():
    # Default (no URL, no mock flag): degrade to empty rather than fabricate a failed run.
    assert asyncio.run(WorkflowClient().get_execution_log("run-42")) == {}


def test_live_enrichment_merges_run_record(monkeypatch):
    """S-2: with a base URL, get_execution_log merges run_status/run_error/summary.status from
    the run record onto the execution-log — the branch the mock path never exercises."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/execution-log"):
            return httpx.Response(200, json={"status": "ok", "data": {"events": []}})
        # GET /api/v1/runs/{id} — a terminal status with no NodeFailed, carrying an error.
        return httpx.Response(200, json={"status": "ok", "data": {
            "status": "TIMED_OUT", "error": "worker did not callback in time"}})

    real = wc.httpx.AsyncClient
    monkeypatch.setattr(
        wc.httpx, "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )

    log = asyncio.run(WorkflowClient("http://cp.local").get_execution_log("run-9"))
    assert log["run_status"] == "TIMED_OUT"
    assert log["summary"]["status"] == "TIMED_OUT"
    assert log["run_error"] == "worker did not callback in time"
    assert log["run_id"] == "run-9"
