import sys
import os
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


@dataclass
class FakeExecuteTaskOutput:
    task_id: str = "t1"
    status: TaskStatus = TaskStatus.SUCCESS
    outputs: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 100.0
    output_reference: str = ""


class FakeExecuteTaskUseCase:
    def __init__(self, output=None):
        self._output = output or FakeExecuteTaskOutput()
        self.called_with = None

    def execute(self, input):
        self.called_with = input
        return self._output


def _make_client(exec_uc=None):
    container = {}
    if exec_uc is not None:
        container["execute_task_usecase"] = exec_uc
    app = FastAPI()
    router = create_router(container)
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


# --- POST /execute-async returns 202 ---

def test_execute_async_returns_202():
    uc = FakeExecuteTaskUseCase()
    client = _make_client(exec_uc=uc)

    resp = client.post("/api/v1/execute-async", json={
        "task_id": "t1",
        "action": "execute",
        "inputs": {"key": "val"},
        "parameters": {},
        "callback_url": "http://executor:8004/api/v1/tasks/callback",
    })

    assert resp.status_code == 202
    body = resp.json()
    assert body["task_id"] == "t1"
    assert body["accepted"] is True


# --- Background task calls use case ---

def test_execute_async_calls_use_case():
    uc = FakeExecuteTaskUseCase()
    client = _make_client(exec_uc=uc)

    client.post("/api/v1/execute-async", json={
        "task_id": "t-async",
        "action": "execute",
        "inputs": {"a": 1},
        "parameters": {"b": 2},
        "correlation_id": "corr-1",
        "callback_url": "http://localhost:8004/api/v1/tasks/callback",
    })

    # FastAPI TestClient runs background tasks synchronously before returning
    assert uc.called_with is not None
    assert uc.called_with.task_id == "t-async"
    assert uc.called_with.inputs == {"a": 1}


# --- Callback POST is made ---

def test_execute_async_sends_callback():
    uc = FakeExecuteTaskUseCase(output=FakeExecuteTaskOutput(
        task_id="t-cb",
        status=TaskStatus.SUCCESS,
        outputs={"uri": "s3://result"},
        duration_ms=50.0,
    ))
    client = _make_client(exec_uc=uc)

    with patch("worker_sdk.layer3_adapters.controllers.restful.v1.routes.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)

        resp = client.post("/api/v1/execute-async", json={
            "task_id": "t-cb",
            "action": "execute",
            "callback_url": "http://executor:8004/api/v1/tasks/callback",
        })

        assert resp.status_code == 202
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://executor:8004/api/v1/tasks/callback"
        payload = call_args[1]["json"]
        assert payload["task_id"] == "t-cb"
        assert payload["status"] == "success"


# --- No callback_url: no POST attempt ---

def test_execute_async_no_callback_url():
    uc = FakeExecuteTaskUseCase()
    client = _make_client(exec_uc=uc)

    with patch("worker_sdk.layer3_adapters.controllers.restful.v1.routes.httpx") as mock_httpx:
        resp = client.post("/api/v1/execute-async", json={
            "task_id": "t-no-cb",
            "action": "execute",
        })

        assert resp.status_code == 202
        mock_httpx.Client.assert_not_called()


# --- SA-1943 HIGH-1: async input-resolution failure posts an ERROR callback ---

def test_execute_async_resolution_failure_posts_error_callback():
    """An input-resolution failure on the async path (e.g. the SA-1943 OOM
    size-guard raising, or an HTTP/connection error) must post an ERROR
    callback — NOT die as a silent unhandled BackgroundTask that hangs the node
    until its deadline. Async-first: the sync /execute path already surfaces
    this as a 500 the CP sees."""
    from worker_sdk.layer1_domain.exceptions import FileRefResolutionError

    class _RaisingResolver:
        def resolve_inputs(self, inputs):
            raise FileRefResolutionError("file-backed input exceeds worker cap (OOM guard)")

    uc = FakeExecuteTaskUseCase()
    container = {
        "execute_task_usecase": uc,
        "_dependencies": {"file_ref_resolver": _RaisingResolver()},
    }
    app = FastAPI()
    app.include_router(create_router(container), prefix="/api/v1")
    client = TestClient(app)

    with patch("worker_sdk.layer3_adapters.controllers.restful.v1.routes.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)

        resp = client.post("/api/v1/execute-async", json={
            "task_id": "t-oom",
            "action": "execute",
            "inputs": {"data": {"__file_ref": True, "file_id": "f-big"}},
            "callback_url": "http://executor:8004/api/v1/tasks/callback",
        })

    assert resp.status_code == 202
    # An ERROR callback was posted (the node fails cleanly, not hangs).
    mock_client.post.assert_called_once()
    payload = mock_client.post.call_args[1]["json"]
    assert payload["task_id"] == "t-oom"
    assert payload["status"] == "error"
    assert "OOM guard" in payload["error"]
    # The use case must never run — resolution failed first.
    assert uc.called_with is None


# --- Callback failure does not crash ---

def test_execute_async_callback_failure_does_not_crash():
    uc = FakeExecuteTaskUseCase()
    client = _make_client(exec_uc=uc)

    with patch("worker_sdk.layer3_adapters.controllers.restful.v1.routes.httpx") as mock_httpx:
        mock_client = MagicMock()
        mock_client.post.side_effect = Exception("connection refused")
        mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
        mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)

        resp = client.post("/api/v1/execute-async", json={
            "task_id": "t-fail-cb",
            "action": "execute",
            "callback_url": "http://dead-host:9999/cb",
        })

        # Should still return 202 — callback failure is swallowed
        assert resp.status_code == 202
