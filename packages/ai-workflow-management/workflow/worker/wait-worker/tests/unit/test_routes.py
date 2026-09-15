from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.layer4_frameworks.config import settings as worker_settings
from app.layer2_application.features.execute_task import ExecuteTaskUseCase
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    WAIT_INPUT_SCHEMA,
    WAIT_OUTPUT_SCHEMA,
)
from worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase import (
    GetWorkerInfoUseCase,
)
from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from tests.conftest import DummyLogger, DummyMonitor


@pytest.fixture(autouse=True)
def _use_worker_settings(monkeypatch):
    """Ensure SDK modules see this worker's settings (WORKER_TYPE='wait')."""
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


def _build_container():
    """Build a minimal container for testing routes."""
    logger = DummyLogger()
    monitor = DummyMonitor()

    return {
        "execute_task_usecase": ExecuteTaskUseCase(logger=logger, monitor=monitor),
        "get_worker_info_usecase": GetWorkerInfoUseCase(
            logger=logger,
            input_schema=WAIT_INPUT_SCHEMA,
            output_schema=WAIT_OUTPUT_SCHEMA,
            app_config=worker_settings,
        ),
        "_dependencies": {},
    }


class TestHealthEndpoints:
    def test_health(self):
        container = _build_container()
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["worker_type"] == "wait"

    def test_ready(self):
        container = _build_container()
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.get("/ready")
        assert resp.status_code == 200
        assert resp.json()["ready"] is True


class TestInfoEndpoint:
    def test_get_info(self):
        container = _build_container()
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.get("/api/v1/info")
        assert resp.status_code == 200
        data = resp.json()
        assert data["worker_type"] == "wait"
        assert data["input_schema"] is not None
        assert data["output_schema"] is not None


class TestExecuteEndpoint:
    @patch("app.layer2_application.features.execute_task.use_cases.execute_task_usecase.time.sleep")
    def test_execute_fixed_duration(self, mock_sleep):
        container = _build_container()
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.post("/api/v1/execute", json={
            "task_id": "t1",
            "action": "wait",
            "inputs": {
                "wait_type": "fixed_duration",
                "duration_seconds": 30,
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t1"
        assert data["status"] == "success"
        assert data["outputs"]["wait_status"] == "completed"
        mock_sleep.assert_called_once_with(30)

    def test_execute_manual_approval(self):
        container = _build_container()
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.post("/api/v1/execute", json={
            "task_id": "t2",
            "action": "wait",
            "inputs": {
                "wait_type": "manual_approval",
                "reason": "Need manager sign-off",
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "manager sign-off" in data["outputs"]["message"].lower()

    def test_execute_unknown_type_returns_error(self):
        container = _build_container()
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.post("/api/v1/execute", json={
            "task_id": "t3",
            "action": "wait",
            "inputs": {"wait_type": "invalid"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
