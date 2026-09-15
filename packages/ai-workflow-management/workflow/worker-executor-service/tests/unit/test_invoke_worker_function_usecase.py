"""Tests for InvokeWorkerFunctionUseCase — the executor→worker function hop.

Regression for 2026-06-08: the use case was gRPC-only (importing the absent
``workflow_proto`` module), so the hop always failed with
``No module named 'workflow_proto'`` even though workers run over HTTP. It now
follows ``TRANSPORT_MODE`` (default ``http``) and POSTs to the worker's
``/api/v1/functions/{name}/invoke`` route.
"""

import sys
import os
from dataclasses import dataclass
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.layer2_application.features.invoke_worker_function.use_cases.invoke_worker_function_usecase import (
    InvokeWorkerFunctionUseCase,
    InvokeWorkerFunctionInput,
)


class DummyLogger:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def debug(self, *a, **k): pass


@dataclass
class _Fn:
    name: str


@dataclass
class _Worker:
    endpoint_url: str
    functions: list
    worker_type: str = "gateway-worker"


class _Registry:
    def __init__(self, worker):
        self._w = worker

    def get_by_worker_type(self, worker_type):
        return self._w


class _Cfg:
    def __init__(self, transport="http", timeout=120):
        self.TRANSPORT_MODE = transport
        self.TASK_TIMEOUT_SECONDS = timeout


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _FakeAsyncClient:
    """Captures the POST and returns a canned worker function-invoke response."""

    captured: dict = {}
    payload: dict = {"status": "success", "result": {"status_code": 200, "body": {"ok": True}}}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        _FakeAsyncClient.captured = {"url": url, "json": json}
        return _FakeResp(_FakeAsyncClient.payload)


def _uc(worker, transport="http"):
    return InvokeWorkerFunctionUseCase(
        logger=DummyLogger(), registry_store=_Registry(worker), app_config=_Cfg(transport),
    )


async def test_http_invoke_posts_to_function_route_and_maps_response():
    _FakeAsyncClient.captured = {}
    worker = _Worker(endpoint_url="http://localhost:35001/", functions=[_Fn("call_internal")])
    with patch("httpx.AsyncClient", _FakeAsyncClient):
        out = await _uc(worker, "http").execute(InvokeWorkerFunctionInput(
            worker_type="gateway-worker",
            function_name="call_internal",
            params={"service": "exec-health", "endpoint": "/health"},
        ))

    # POSTs to the worker's HTTP function-invoke route (endpoint rstrip'd) with {"params": ...}
    assert _FakeAsyncClient.captured["url"] == "http://localhost:35001/api/v1/functions/call_internal/invoke"
    assert _FakeAsyncClient.captured["json"] == {"params": {"service": "exec-health", "endpoint": "/health"}}
    # The worker's {status, result, error} is mapped faithfully onto the output.
    assert out.status == "success"
    assert out.result == {"status_code": 200, "body": {"ok": True}}
    assert out.error == ""


async def test_unknown_function_rejected_before_dispatch():
    _FakeAsyncClient.captured = {}
    worker = _Worker(endpoint_url="http://localhost:35001", functions=[_Fn("call_internal")])
    with patch("httpx.AsyncClient", _FakeAsyncClient):
        out = await _uc(worker).execute(InvokeWorkerFunctionInput(
            worker_type="gateway-worker", function_name="nope", params={}))
    assert out.status == "error"
    assert "not declared" in out.error
    assert "url" not in _FakeAsyncClient.captured  # never dispatched


async def test_missing_endpoint_errors():
    worker = _Worker(endpoint_url="", functions=[_Fn("call_internal")])
    out = await _uc(worker).execute(InvokeWorkerFunctionInput(
        worker_type="gateway-worker", function_name="call_internal", params={}))
    assert out.status == "error"
    assert "no endpoint" in out.error.lower()


async def test_grpc_mode_does_not_take_http_path():
    """TRANSPORT_MODE=grpc routes to the gRPC path, not HTTP. (It currently
    errors because ``workflow_proto`` is absent — but the point is the HTTP
    client is never touched, proving the transport switch.)"""
    _FakeAsyncClient.captured = {}
    worker = _Worker(endpoint_url="localhost:50053", functions=[_Fn("call_internal")])
    with patch("httpx.AsyncClient", _FakeAsyncClient):
        out = await _uc(worker, "grpc").execute(InvokeWorkerFunctionInput(
            worker_type="gateway-worker", function_name="call_internal", params={}))
    assert "url" not in _FakeAsyncClient.captured  # HTTP path not taken
    assert out.status == "error"  # gRPC path errors (no workflow_proto)
