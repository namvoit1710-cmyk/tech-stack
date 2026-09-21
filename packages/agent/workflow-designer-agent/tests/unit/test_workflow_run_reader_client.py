from __future__ import annotations

from app.layer4_frameworks.clients.workflow_run_reader_client import (
    HttpWorkflowRunReader,
)


class _FakeResp:
    def __init__(self, body):
        self._b = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


class _FakeClient:
    """Matches a request URL by suffix to a scripted body; records calls."""

    def __init__(self, routes):
        self._routes = routes
        self.calls: list[tuple] = []

    async def get(self, url, params=None):
        self.calls.append((url, params))
        for suffix, body in self._routes:
            if url.endswith(suffix):
                return _FakeResp(body)
        return _FakeResp({})

    async def aclose(self):
        pass


async def test_resolve_prefers_failed_run_filtered_by_name_newest_first():
    routes = [("/api/v1/runs", {"status": "ok", "data": {"items": [
        {"id": "r-old", "workflow_name": "Weather", "started_at": "2024-01-01"},
        {"id": "r-new", "workflow_name": "Weather", "started_at": "2024-02-01"},
        {"id": "r-other", "workflow_name": "Sales", "started_at": "2024-03-01"},
    ]}})]
    client = _FakeClient(routes)
    reader = HttpWorkflowRunReader(base_url="http://cp", client=client)

    rid = await reader.resolve_run_id(workflow_name="weather")

    assert rid == "r-new"                             # name-filtered, newest by started_at
    assert client.calls[0][1]["status"] == "FAILED"    # asked for failed runs first


async def test_resolve_returns_empty_without_base_or_reference():
    assert await HttpWorkflowRunReader(base_url="").resolve_run_id(workflow_name="x") == ""
    assert await HttpWorkflowRunReader(base_url="http://cp").resolve_run_id() == ""


async def test_execution_log_enriched_with_run_status_and_error():
    routes = [
        ("/execution-log", {"status": "ok", "data": {"summary": {"node_counts": {"failed": 1}}}}),
        ("/api/v1/runs/r-9", {"status": "ok", "data": {"status": "FAILED", "error": "boom"}}),
    ]
    reader = HttpWorkflowRunReader(base_url="http://cp", client=_FakeClient(routes))

    log = await reader.get_execution_log("r-9")

    assert log["run_status"] == "FAILED"
    assert log["run_error"] == "boom"
    assert log["run_id"] == "r-9"


async def test_execution_log_empty_without_base():
    assert await HttpWorkflowRunReader(base_url="").get_execution_log("r-9") == {}
