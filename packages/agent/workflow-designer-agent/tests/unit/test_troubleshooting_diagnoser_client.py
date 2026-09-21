from __future__ import annotations

from app.layer4_frameworks.clients.troubleshooting_diagnoser_client import (
    RemoteWorkflowDiagnoser,
)


class _FakeResp:
    def __init__(self, body):
        self._b = body

    def raise_for_status(self):
        pass

    def json(self):
        return self._b


class _FakeClient:
    """Records the POST it receives and replies with a scripted body (or raises)."""

    def __init__(self, body=None, raises=None):
        self._body = body
        self._raises = raises
        self.calls: list[tuple] = []

    async def post(self, url, json=None):
        self.calls.append((url, json))
        if self._raises is not None:
            raise self._raises
        return _FakeResp(self._body)

    async def aclose(self):
        pass


# The troubleshooting-agent's /execute response: the DiagnosticReport lands in agent_data.
_REPORT = {
    "run_id": "run-9",
    "category": "agent_service_communication",
    "owner": "dev",
    "severity": "high",
    "root_cause": "HTTP 500 from the worker",
    "recommended_fix": "point the node at a reachable URL",
    "explanation": "The task node called an endpoint that returned 500.",
    "confidence": 0.9,
    "failed_node_ids": ["b"],
    "evidence": ["node b -> 500"],
}


async def test_maps_agent_data_report_into_a_verdict():
    client = _FakeClient(body={"message": "…", "status": "success", "agent_data": _REPORT})
    diag = RemoteWorkflowDiagnoser(base_url="http://ts", client=client)

    verdict = await diag.diagnose({"run_id": "run-9", "workflow_id": "wf-7"}, run_id="run-9")

    assert verdict.has_failure()
    assert verdict.category == "agent_service_communication"
    assert verdict.root_cause == "HTTP 500 from the worker"
    assert verdict.recommended_fix == "point the node at a reachable URL"
    assert verdict.failed_node_ids == ["b"]
    assert verdict.run_id == "run-9"


async def test_posts_run_reference_to_execute_endpoint():
    client = _FakeClient(body={"agent_data": _REPORT})
    diag = RemoteWorkflowDiagnoser(base_url="http://ts/", client=client)

    await diag.diagnose({"workflow_id": "wf-7", "workflow_name": "Weather"}, run_id="run-9")

    url, payload = client.calls[0]
    assert url == "http://ts/api/v1/execute"          # trailing slash normalized
    assert payload["parameters"]["run_id"] == "run-9"
    assert payload["parameters"]["workflow_id"] == "wf-7"
    assert payload["parameters"]["workflow_name"] == "Weather"


async def test_no_base_url_degrades_to_empty_verdict():
    diag = RemoteWorkflowDiagnoser(base_url="")
    verdict = await diag.diagnose({"run_id": "run-9"}, run_id="run-9")
    assert not verdict.has_failure()
    assert verdict.run_id == "run-9"


async def test_no_reference_does_not_call_the_service():
    client = _FakeClient(body={"agent_data": _REPORT})
    diag = RemoteWorkflowDiagnoser(base_url="http://ts", client=client)
    verdict = await diag.diagnose({}, run_id="")
    assert not verdict.has_failure()
    assert client.calls == []


async def test_missing_agent_data_degrades_to_empty_verdict():
    client = _FakeClient(body={"status": "error", "error": "boom"})
    diag = RemoteWorkflowDiagnoser(base_url="http://ts", client=client)
    verdict = await diag.diagnose({"run_id": "run-9"}, run_id="run-9")
    assert not verdict.has_failure()


async def test_network_error_degrades_to_empty_verdict():
    client = _FakeClient(raises=RuntimeError("connection refused"))
    diag = RemoteWorkflowDiagnoser(base_url="http://ts", client=client)
    verdict = await diag.diagnose({"run_id": "run-9"}, run_id="run-9")
    assert not verdict.has_failure()
    assert verdict.run_id == "run-9"
