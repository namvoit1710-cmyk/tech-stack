"""Tests for multi-type worker support via NodeTypeDefinition."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from worker_sdk.layer1_domain.entities.node_type_definition import (
    NodeTypeDefinition, VALID_NODE_KINDS,
)
from worker_sdk.layer1_domain.value_objects.node_kind import NodeKind
from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction
from worker_sdk.bootstrap import build_app_container
from worker_sdk.layer3_adapters.controllers.worker_server import create_worker_app
from worker_sdk.layer3_adapters.controllers.worker_headless import run_headless_worker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _handler_send_message(inputs, parameters):
    return {"message_id": "msg-123", "channel": inputs.get("channel", "")}


async def _handler_create_channel(inputs, parameters):
    return {"channel_id": "ch-456", "name": inputs.get("name", "")}


def _handler_sync(inputs, parameters):
    """Sync handler to test both sync and async support."""
    return {"result": "sync-ok"}


async def _handler_that_fails(inputs, parameters):
    raise ValueError("Something went wrong in handler")


def _build_node_types():
    return [
        NodeTypeDefinition(
            worker_type="teams_send_message",
            name="Teams: Send Message",
            icon="MessageSquare",
            color="#6264A7",
            input_schema=[{"key": "channel", "type": "string"}],
            output_schema=[{"key": "message_id", "type": "string"}],
            handler=_handler_send_message,
        ),
        NodeTypeDefinition(
            worker_type="teams_create_channel",
            name="Teams: Create Channel",
            icon="Hash",
            color="#6264A7",
            input_schema=[{"key": "name", "type": "string"}],
            output_schema=[{"key": "channel_id", "type": "string"}],
            handler=_handler_create_channel,
        ),
    ]


def _build_multi_type_client(node_types=None):
    """Build a TestClient with multi-type node_types and no real registry."""
    container = build_app_container(node_types=node_types or _build_node_types())
    # Remove the registry so the lifespan doesn't try to register over HTTP
    container["_dependencies"].pop("worker_registry", None)
    app = create_worker_app(container)
    return TestClient(app)


# ---------------------------------------------------------------------------
# Entity tests
# ---------------------------------------------------------------------------

class TestNodeTypeDefinition:
    def test_create_with_defaults(self):
        nt = NodeTypeDefinition(worker_type="my_worker")
        assert nt.worker_type == "my_worker"
        assert nt.name == ""
        assert nt.node_class == "BUSINESS"
        assert nt.icon == "Cog"
        assert nt.color == "#3B82F6"
        assert nt.tags == []
        assert nt.capabilities == []
        assert nt.ports is None
        assert nt.input_schema is None
        assert nt.output_schema is None
        assert nt.functions == []
        assert nt.handler is None

    def test_create_with_all_fields(self):
        handler = _handler_send_message
        nt = NodeTypeDefinition(
            worker_type="teams_send",
            name="Teams: Send",
            description="Send a message",
            version="2.0.0",
            node_class="BUSINESS",
            icon="Send",
            color="#FF0000",
            tags=["teams", "messaging"],
            capabilities=[{"domain": "teams", "action": "send"}],
            ports={"in": [{"id": "in", "label": "In"}], "out": [{"id": "out", "label": "Out"}]},
            input_schema=[{"key": "msg", "type": "string"}],
            output_schema=[{"key": "id", "type": "string"}],
            handler=handler,
        )
        assert nt.worker_type == "teams_send"
        assert nt.name == "Teams: Send"
        assert nt.version == "2.0.0"
        assert nt.tags == ["teams", "messaging"]
        assert nt.handler is handler

    def test_blank_worker_type_raises(self):
        with pytest.raises(ValueError, match="worker_type is required"):
            NodeTypeDefinition(worker_type="")

    def test_whitespace_worker_type_raises(self):
        with pytest.raises(ValueError, match="worker_type is required"):
            NodeTypeDefinition(worker_type="   ")


# ---------------------------------------------------------------------------
# Bootstrap tests
# ---------------------------------------------------------------------------

class TestBootstrapNodeTypes:
    def test_node_type_registry_built(self):
        container = build_app_container(node_types=_build_node_types())
        deps = container["_dependencies"]
        assert "node_type_registry" in deps
        registry = deps["node_type_registry"]
        assert "teams_send_message" in registry
        assert "teams_create_channel" in registry
        assert registry["teams_send_message"].name == "Teams: Send Message"

    def test_no_node_types_means_no_registry(self):
        container = build_app_container()
        deps = container["_dependencies"]
        assert "node_type_registry" not in deps

    def test_empty_node_types_means_no_registry(self):
        container = build_app_container(node_types=[])
        deps = container["_dependencies"]
        assert "node_type_registry" not in deps


# ---------------------------------------------------------------------------
# Routing tests (execute endpoint)
# ---------------------------------------------------------------------------

class TestMultiTypeRouting:
    def test_execute_routes_to_correct_handler(self):
        client = _build_multi_type_client()
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-1",
            "action": "execute",
            "inputs": {"channel": "general"},
            "worker_type": "teams_send_message",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["outputs"]["message_id"] == "msg-123"
        assert data["outputs"]["channel"] == "general"

    def test_execute_routes_second_handler(self):
        client = _build_multi_type_client()
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-2",
            "action": "execute",
            "inputs": {"name": "dev-team"},
            "worker_type": "teams_create_channel",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["outputs"]["channel_id"] == "ch-456"
        assert data["outputs"]["name"] == "dev-team"

    def test_execute_unknown_worker_type_falls_through(self):
        """Unknown worker_type falls back to SDK's built-in ExecuteTaskUseCase (placeholder)."""
        client = _build_multi_type_client()
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-3",
            "action": "execute",
            "inputs": {},
            "worker_type": "unknown_type",
        })
        assert resp.status_code == 200
        data = resp.json()
        # Falls through to the built-in ExecuteTaskUseCase which returns placeholder
        assert data["task_id"] == "t-3"

    def test_execute_no_worker_type_falls_through(self):
        """No worker_type in payload falls back to existing behavior."""
        client = _build_multi_type_client()
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-4",
            "action": "execute",
            "inputs": {},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["task_id"] == "t-4"

    def test_execute_handler_error_returns_failed(self):
        node_types = [
            NodeTypeDefinition(
                worker_type="failing_type",
                handler=_handler_that_fails,
            ),
        ]
        client = _build_multi_type_client(node_types=node_types)
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-5",
            "action": "execute",
            "inputs": {},
            "worker_type": "failing_type",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "error"
        assert "Something went wrong" in data["error"]

    def test_execute_sync_handler(self):
        node_types = [
            NodeTypeDefinition(
                worker_type="sync_type",
                handler=_handler_sync,
            ),
        ]
        client = _build_multi_type_client(node_types=node_types)
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-6",
            "action": "execute",
            "inputs": {},
            "worker_type": "sync_type",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert data["outputs"]["result"] == "sync-ok"

    def test_execute_measures_duration(self):
        client = _build_multi_type_client()
        resp = client.post("/api/v1/execute", json={
            "task_id": "t-7",
            "action": "execute",
            "inputs": {"channel": "test"},
            "worker_type": "teams_send_message",
        })
        data = resp.json()
        assert data["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Async execute tests
# ---------------------------------------------------------------------------

class TestMultiTypeAsyncRouting:
    def test_execute_async_accepted(self):
        client = _build_multi_type_client()
        resp = client.post("/api/v1/execute-async", json={
            "task_id": "t-async-1",
            "action": "execute",
            "inputs": {"channel": "general"},
            "worker_type": "teams_send_message",
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["task_id"] == "t-async-1"
        assert data["accepted"] is True


# ---------------------------------------------------------------------------
# Info types endpoint
# ---------------------------------------------------------------------------

class TestInfoTypesEndpoint:
    def test_info_types_returns_all_types(self):
        client = _build_multi_type_client()
        resp = client.get("/api/v1/info/types")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        types = {t["worker_type"] for t in data}
        assert types == {"teams_send_message", "teams_create_channel"}

    def test_info_types_includes_metadata(self):
        client = _build_multi_type_client()
        resp = client.get("/api/v1/info/types")
        data = resp.json()
        send = next(t for t in data if t["worker_type"] == "teams_send_message")
        assert send["name"] == "Teams: Send Message"
        assert send["icon"] == "MessageSquare"
        assert send["color"] == "#6264A7"
        assert send["input_schema"] == [{"key": "channel", "type": "string"}]
        assert send["output_schema"] == [{"key": "message_id", "type": "string"}]

    def test_info_types_not_available_in_single_type_mode(self):
        """GET /info/types should 404 when no node_types are configured."""
        container = build_app_container()
        container["_dependencies"].pop("worker_registry", None)
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.get("/api/v1/info/types")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Multi-registration lifecycle (server mode)
# ---------------------------------------------------------------------------

class TestMultiRegistrationLifecycle:
    def test_multi_type_app_starts_without_registry(self):
        """App with node_types but no registry starts fine (no registration attempt)."""
        client = _build_multi_type_client()
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_single_type_app_still_works(self):
        """App without node_types continues to work as before."""
        container = build_app_container()
        container["_dependencies"].pop("worker_registry", None)
        app = create_worker_app(container)
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Multi-registration lifecycle (headless mode)
# ---------------------------------------------------------------------------

class TestHeadlessMultiRegistration:
    @pytest.mark.asyncio
    async def test_headless_registers_multiple_types(self, monkeypatch):
        registry = AsyncMock()
        registry.register = AsyncMock(side_effect=["w-1", "w-2"])
        registry.heartbeat = AsyncMock()
        registry.deregister = AsyncMock()
        registry.close = AsyncMock()

        node_types = _build_node_types()
        node_type_registry = {nt.worker_type: nt for nt in node_types}

        container = {
            "_dependencies": {
                "worker_registry": registry,
                "logger": MagicMock(),
                "node_type_registry": node_type_registry,
            }
        }

        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            MagicMock(
                WORKER_TYPE="test",
                WORKER_VERSION="1.0",
                SDK_VERSION="1.0.0",
                HEARTBEAT_INTERVAL_SECONDS=0.01,
            ),
        )

        task = asyncio.create_task(run_headless_worker(container))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Should have registered 2 types
        assert registry.register.call_count == 2
        # Should have heartbeated both
        assert registry.heartbeat.call_count >= 2
        # Should have deregistered both
        assert registry.deregister.call_count == 2

    @pytest.mark.asyncio
    async def test_headless_single_type_still_works(self, monkeypatch):
        registry = AsyncMock()
        registry.register = AsyncMock(return_value="w-1")
        registry.heartbeat = AsyncMock()
        registry.deregister = AsyncMock()
        registry.close = AsyncMock()

        container = {
            "_dependencies": {
                "worker_registry": registry,
                "logger": MagicMock(),
            }
        }

        monkeypatch.setattr(
            "worker_sdk.layer3_adapters.controllers.worker_headless.settings",
            MagicMock(
                WORKER_TYPE="test",
                WORKER_VERSION="1.0",
                SDK_VERSION="1.0.0",
                WORKER_NAME="",
                WORKER_DESCRIPTION="",
                WORKER_NODE_CLASS="BUSINESS",
                WORKER_ICON="Cog",
                WORKER_COLOR="#3B82F6",
                WORKER_TAGS="",
                REGISTRY_URL="http://localhost:8000",
                HEARTBEAT_INTERVAL_SECONDS=0.01,
                get_tags_list=MagicMock(return_value=[]),
            ),
        )

        task = asyncio.create_task(run_headless_worker(container))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Single-type: exactly 1 registration
        assert registry.register.call_count == 1
        assert registry.deregister.call_count == 1


# ---------------------------------------------------------------------------
# Import test
# ---------------------------------------------------------------------------

class TestPublicExports:
    def test_node_type_definition_importable_from_sdk(self):
        from worker_sdk import NodeTypeDefinition
        nt = NodeTypeDefinition(worker_type="test")
        assert nt.worker_type == "test"


def test_kind_defaults_to_action():
    ntd = NodeTypeDefinition(worker_type="demo")
    assert ntd.kind == "action"
    assert ntd.kind is NodeKind.ACTION

def test_kind_accepts_valid_vocab():
    for k in VALID_NODE_KINDS:
        assert NodeTypeDefinition(worker_type="demo", kind=k).kind == k

def test_kind_accepts_plain_string_backcompat():
    """A worker constructing NodeTypeDefinition(kind="read") as a plain
    string (the pre-existing contract) must still work unchanged."""
    ntd = NodeTypeDefinition(worker_type="demo", kind="read")
    assert ntd.kind == "read"
    assert ntd.kind is NodeKind.READ

def test_kind_rejects_unknown():
    import pytest
    with pytest.raises(ValueError, match="kind"):
        NodeTypeDefinition(worker_type="demo", kind="bogus")

def test_kind_rejects_invented_kind_that_actually_shipped():
    """"write" is not a real kind -- it is the invented value that shipped
    before this fix. Must raise, same as any other unknown value."""
    import pytest
    with pytest.raises(ValueError, match="kind"):
        NodeTypeDefinition(worker_type="demo", kind="write")
