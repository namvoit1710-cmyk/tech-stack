import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router
from worker_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import (
    BaseInputDto,
    BaseOutputDto,
    BaseOutputDataclass,
)
from worker_sdk.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    ExecuteTaskUseCase,
    ExecuteTaskCommand,
    ExecuteTaskResult,
)
from worker_sdk.layer2_application.features.get_worker_info.use_cases.get_worker_info_usecase import (
    GetWorkerInfoUseCase,
    GetWorkerInfoCommand,
    GetWorkerInfoResult,
)
from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus


# ==================== Helpers ====================

class DummyLogger:
    def info(self, message: str, **kwargs): pass
    def error(self, message: str, **kwargs): pass
    def warning(self, message: str, **kwargs): pass
    def debug(self, message: str, **kwargs): pass


class DummyMonitor:
    def track(self, metric: str, value: float): pass


def _build_container():
    """Build a minimal container with real use cases."""
    logger = DummyLogger()
    monitor = DummyMonitor()
    return {
        "execute_task_usecase": ExecuteTaskUseCase(logger=logger, monitor=monitor),
        "get_worker_info_usecase": GetWorkerInfoUseCase(logger=logger),
        "_dependencies": {},
    }


def _create_test_client(container=None):
    if container is None:
        container = _build_container()
    app = create_worker_app(container)
    return TestClient(app, raise_server_exceptions=False)


# ==================== BaseInputDto ====================

def test_base_input_to_dataclass():
    class MyInput(BaseInputDto):
        name: str
        value: int

    from dataclasses import dataclass

    @dataclass
    class MyInputDC:
        name: str
        value: int

    pydantic_obj = MyInput(name="test", value=42)
    dc_obj = pydantic_obj.to_dataclass(MyInputDC)
    assert dc_obj.name == "test"
    assert dc_obj.value == 42


# ==================== BaseOutputDto ====================

def test_base_output_from_dataclass():
    from dataclasses import dataclass

    @dataclass
    class MyOutputDC:
        result: str
        count: int

    class MyOutput(BaseOutputDto):
        result: str
        count: int

    dc_obj = MyOutputDC(result="ok", count=5)
    pydantic_obj = MyOutput.from_dataclass(dc_obj)
    assert pydantic_obj.result == "ok"
    assert pydantic_obj.count == 5


# ==================== BaseOutputDataclass ====================

def test_base_output_dataclass_to_pydantic():
    from dataclasses import dataclass

    @dataclass
    class MyOutputDC(BaseOutputDataclass):
        name: str
        value: int

    class MyOutputPydantic(BaseOutputDto):
        name: str
        value: int

    dc_obj = MyOutputDC(name="test", value=10)
    pydantic_obj = dc_obj.to_pydantic(MyOutputPydantic)
    assert pydantic_obj.name == "test"
    assert pydantic_obj.value == 10


# ==================== Health & Ready endpoints ====================

def test_health_endpoint():
    client = _create_test_client()
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "worker_type" in data
    assert "version" in data
    assert "sdk_version" in data


def test_ready_endpoint():
    client = _create_test_client()
    response = client.get("/ready")
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True


# ==================== /api/v1/info endpoint ====================

def test_info_endpoint():
    client = _create_test_client()
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    data = response.json()
    assert "worker_type" in data
    assert "version" in data
    assert "sdk_version" in data


# ==================== /api/v1/execute endpoint ====================

def test_execute_endpoint_without_executor():
    client = _create_test_client()
    payload = {"task_id": "t1", "action": "run"}
    response = client.post("/api/v1/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "t1"
    assert data["status"] == "success"
    assert "placeholder" in data["outputs"].get("message", "")


def test_execute_endpoint_with_all_fields():
    client = _create_test_client()
    payload = {
        "task_id": "t2",
        "action": "process",
        "inputs": {"key": "val"},
        "parameters": {"p": 1},
        "correlation_id": "corr-1",
    }
    response = client.post("/api/v1/execute", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == "t2"


def test_execute_endpoint_invalid_payload():
    client = _create_test_client()
    response = client.post("/api/v1/execute", json={})
    assert response.status_code == 422


# ==================== Router with empty container ====================

def test_router_without_features():
    """Router with empty container produces no feature routes."""
    from fastapi import FastAPI
    router = create_router({})
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    response = client.get("/api/v1/info")
    assert response.status_code in (404, 405)
    response = client.post("/api/v1/execute", json={"task_id": "t", "action": "x"})
    assert response.status_code in (404, 405)


# ==================== create_worker_app ====================

def test_create_worker_app_returns_fastapi():
    from fastapi import FastAPI
    container = _build_container()
    app = create_worker_app(container)
    assert isinstance(app, FastAPI)


def test_create_worker_app_exposes_dependencies():
    container = _build_container()
    container["_dependencies"] = {"logger": DummyLogger()}
    app = create_worker_app(container)
    assert hasattr(app.state, "dependencies")
    assert "logger" in app.state.dependencies


# ==================== Lifespan tests ====================

def test_lifespan_with_registry_registers_and_deregisters(monkeypatch):
    """Lifespan registers on startup, heartbeats, and deregisters on shutdown."""
    registry = AsyncMock()
    registry.register = AsyncMock(return_value="w-srv-1")
    registry.heartbeat = AsyncMock()
    registry.deregister = AsyncMock()
    registry.close = AsyncMock()

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_server.settings",
        MagicMock(
            APP_NAME="Test",
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=35000,
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    container = _build_container()
    container["_dependencies"] = {"worker_registry": registry}

    app = create_worker_app(container)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    registry.register.assert_called_once()
    registry.deregister.assert_called_once_with("w-srv-1")
    registry.close.assert_called_once()


def test_lifespan_without_registry(monkeypatch):
    """Lifespan works when no registry is provided."""
    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_server.settings",
        MagicMock(
            APP_NAME="Test",
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=35000,
            HEARTBEAT_INTERVAL_SECONDS=30,
        ),
    )

    container = _build_container()
    container["_dependencies"] = {}

    app = create_worker_app(container)
    with TestClient(app) as client:
        response = client.get("/ready")
        assert response.status_code == 200


def test_lifespan_registration_failure(monkeypatch, capsys):
    """Lifespan continues even when registration fails."""
    registry = AsyncMock()
    registry.register = AsyncMock(side_effect=Exception("conn refused"))
    registry.close = AsyncMock()

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_server.settings",
        MagicMock(
            APP_NAME="Test",
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=35000,
            HEARTBEAT_INTERVAL_SECONDS=30,
        ),
    )

    container = _build_container()
    container["_dependencies"] = {"worker_registry": registry}

    app = create_worker_app(container)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    captured = capsys.readouterr()
    assert "Registration failed" in captured.out


def test_lifespan_deregistration_failure(monkeypatch, capsys):
    """Lifespan handles deregistration failure gracefully."""
    registry = AsyncMock()
    registry.register = AsyncMock(return_value="w-srv-2")
    registry.heartbeat = AsyncMock()
    registry.deregister = AsyncMock(side_effect=Exception("dereg error"))
    registry.close = AsyncMock()

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_server.settings",
        MagicMock(
            APP_NAME="Test",
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=35000,
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    container = _build_container()
    container["_dependencies"] = {"worker_registry": registry}

    app = create_worker_app(container)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200

    captured = capsys.readouterr()
    assert "Deregistration failed" in captured.out


def test_lifespan_heartbeat_failure(monkeypatch, capsys):
    """Lifespan logs heartbeat failures without crashing."""
    registry = AsyncMock()
    registry.register = AsyncMock(return_value="w-srv-3")
    registry.heartbeat = AsyncMock(side_effect=Exception("hb timeout"))
    registry.deregister = AsyncMock()
    registry.close = AsyncMock()

    monkeypatch.setattr(
        "worker_sdk.layer3_adapters.controllers.worker_server.settings",
        MagicMock(
            APP_NAME="Test",
            WORKER_TYPE="test",
            WORKER_VERSION="1.0",
            SDK_VERSION="1.0.0",
            SERVER_HOST="0.0.0.0",
            SERVER_PORT=35000,
            HEARTBEAT_INTERVAL_SECONDS=0.01,
        ),
    )

    container = _build_container()
    container["_dependencies"] = {"worker_registry": registry}

    app = create_worker_app(container)
    with TestClient(app) as client:
        import time
        time.sleep(0.05)
        response = client.get("/health")
        assert response.status_code == 200

    captured = capsys.readouterr()
    assert "Heartbeat failed" in captured.out
