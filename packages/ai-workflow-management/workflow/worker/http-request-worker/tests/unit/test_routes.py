from unittest.mock import MagicMock, patch
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.layer4_frameworks.config import settings as worker_settings
from app.layer2_application.features.execute_task import ExecuteTaskUseCase
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    HTTP_REQUEST_INPUT_SCHEMA,
    HTTP_REQUEST_OUTPUT_SCHEMA,
)
from worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase import (
    GetWorkerInfoUseCase,
)
from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from tests.conftest import DummyLogger, DummyMonitor


@pytest.fixture(autouse=True)
def _use_worker_settings(monkeypatch):
    """Ensure SDK modules see this worker's settings (WORKER_TYPE='http_request')."""
    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_server.settings",
        worker_settings,
    )
    # raising=False: no-op on new worker_sdk (settings moved to app_config injection);
    # still patches older installed versions that read a module-level settings singleton.
    monkeypatch.setattr(
        "worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase.settings",
        worker_settings,
        raising=False,
    )


def _mock_response(status_code=200, headers=None, text="OK", json_data=None, url="https://example.com"):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {"content-type": "text/plain"}
    resp.text = text
    resp.url = url
    resp.elapsed = timedelta(milliseconds=10)
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = Exception("not json")
    import json as _json
    _b = _json.dumps(json_data).encode() if json_data is not None else (text.encode() if isinstance(text,str) else (text or b""))
    resp.iter_bytes = MagicMock(return_value=[_b])
    resp.iter_raw = MagicMock(return_value=[_b])
    return resp


def _build_container(max_response_bytes=None):
    logger = DummyLogger()
    monitor = DummyMonitor()
    return {
        "execute_task_usecase": ExecuteTaskUseCase(
            logger=logger, monitor=monitor, max_response_bytes=max_response_bytes,
        ),
        "get_worker_info_usecase": GetWorkerInfoUseCase(
            logger=logger,
            input_schema=HTTP_REQUEST_INPUT_SCHEMA,
            output_schema=HTTP_REQUEST_OUTPUT_SCHEMA,
            app_config=worker_settings,
        ),
        "_dependencies": {},
    }


class TestHealthEndpoints:
    def test_health(self):
        app = create_worker_app(_build_container())
        resp = TestClient(app).get("/health")
        assert resp.status_code == 200
        assert resp.json()["worker_type"] == "http_request"

    def test_ready(self):
        resp = TestClient(create_worker_app(_build_container())).get("/ready")
        assert resp.json()["ready"] is True


class TestInfoEndpoint:
    def test_get_info(self):
        app = create_worker_app(_build_container())
        resp = TestClient(app).get("/api/v1/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_type"] == "http_request"
        assert data["input_schema"] is not None
        assert data["output_schema"] is not None


class TestExecuteEndpoint:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_execute_get(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.stream.return_value.__enter__.return_value = _mock_response(200, text="ok")
        mock_client_cls.return_value = mock_client

        app = create_worker_app(_build_container())
        resp = TestClient(app).post("/api/v1/execute", json={
            "task_id": "t1",
            "action": "http_request",
            "inputs": {"method": "GET", "url": "https://example.com"},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "success"
        assert resp.json()["outputs"]["status_code"] == 200

    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.httpx.Client")
    def test_execute_async_inherits_response_cap(self, client_cls):
        """SA-1940 sync+async rule — the async /execute-async path runs the SAME
        capped use-case, so an over-cap response fails there too and the failure
        reaches the callback. (The worker's upstream request and the callback POST
        both go through the one patched ``httpx.Client``.)"""
        resp = _mock_response(200, headers={"content-type": "text/plain"})
        resp.iter_raw = MagicMock(return_value=[b"x" * 100_000])  # >> cap
        captured = {}
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        # upstream request → over-cap stream
        client.stream.return_value.__enter__.return_value = resp
        client.stream.return_value.__exit__ = MagicMock(return_value=False)
        # callback POST → capture the delivered payload
        client.post.side_effect = (
            lambda url, json=None, **kw: captured.update(payload=json) or MagicMock(status_code=200)
        )
        client_cls.return_value = client

        app = create_worker_app(_build_container(max_response_bytes=100))
        r = TestClient(app).post("/api/v1/execute-async", json={
            "task_id": "async1",
            "action": "http_request",
            "inputs": {"method": "GET", "url": "https://example.com"},
            "callback_url": "https://cb.example/hook",
        })
        assert r.status_code == 202
        # TestClient runs the background task before returning → callback fired.
        assert captured["payload"]["status"] == "error"
        assert captured["payload"]["outputs"]["response_too_large"] is True

    def test_execute_missing_url(self):
        app = create_worker_app(_build_container())
        resp = TestClient(app).post("/api/v1/execute", json={
            "task_id": "t2",
            "action": "http_request",
            "inputs": {"method": "GET"},
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"
