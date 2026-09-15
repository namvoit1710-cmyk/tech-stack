"""Shared test fixtures for the jira-worker.

Handlers resolve a credential from ``inputs['__credentials'][credential_ref]`` and
then call ``JiraClient`` methods. Tests patch ``JiraClient._request`` (the single
transport chokepoint) with a recorder so no network is touched — this exercises the
operation methods (URL/path/body construction) AND the node handlers end-to-end.
"""

from typing import Any, Dict, Optional

import pytest

from app.layer2_application.services.jira_client import JiraClient

# A valid resolved jira-api-token credential, as the control plane injects it.
CRED_INPUTS: Dict[str, Any] = {
    "__credentials": {
        "default": {
            "type": "json",
            "config": {
                "domain": "https://laidon.atlassian.net",
                "email": "edgar@laidon.com",
                "apiToken": "tok-123",
            },
        }
    },
    "credential_ref": "default",
}


class Recorder:
    """Captures JiraClient._request calls and returns canned responses by path."""

    def __init__(self) -> None:
        self.calls: list[Dict[str, Any]] = []
        self.responses: Dict[str, Any] = {}

    def set(self, path: str, value: Any) -> None:
        self.responses[path] = value

    def last(self) -> Dict[str, Any]:
        return self.calls[-1]


@pytest.fixture
def cred_inputs() -> Dict[str, Any]:
    # Fresh copy per test so handlers can mutate their working dict safely.
    return {
        "__credentials": {"default": dict(CRED_INPUTS["__credentials"]["default"])},
        "credential_ref": "default",
    }


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    recorder = Recorder()

    def fake_request(
        self: JiraClient,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Any = None,
        files: Any = None,
        extra_headers: Optional[dict] = None,
    ) -> Any:
        recorder.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json": json_body,
                "files": files,
                "headers": extra_headers,
            }
        )
        return recorder.responses.get(path, {})

    monkeypatch.setattr(JiraClient, "_request", fake_request)
    return recorder
