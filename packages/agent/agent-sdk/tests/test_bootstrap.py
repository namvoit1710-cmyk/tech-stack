import inspect

import pytest

from tests.helpers.testing import StubLogger, StubMonitor


def test_scan_and_load_features_has_type_annotations():
    from agent_sdk.bootstrap import scan_and_load_features

    hints = {}
    for param_name, param in inspect.signature(
        scan_and_load_features
    ).parameters.items():
        hints[param_name] = param.annotation
    assert (
        hints["features_path"] != inspect.Parameter.empty
    ), "features_path must have a type annotation"
    assert (
        hints["base_module"] != inspect.Parameter.empty
    ), "base_module must have a type annotation"


def test_build_app_container_has_type_annotations():
    from agent_sdk.bootstrap import build_app_container

    hints = {}
    for param_name, param in inspect.signature(build_app_container).parameters.items():
        hints[param_name] = param.annotation
    assert (
        hints["features_path"] != inspect.Parameter.empty
    ), "features_path must have a type annotation"
    assert (
        hints["base_module"] != inspect.Parameter.empty
    ), "base_module must have a type annotation"
    assert (
        hints["extra_dependencies"] != inspect.Parameter.empty
    ), "extra_dependencies must have a type annotation"
    assert (
        hints["agent_graph"] != inspect.Parameter.empty
    ), "agent_graph must have a type annotation"
    assert (
        hints["agent_graph_factory"] != inspect.Parameter.empty
    ), "agent_graph_factory must have a type annotation"
    assert (
        hints["local_tools"] != inspect.Parameter.empty
    ), "local_tools must have a type annotation"


def test_container_has_sdk_features():
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    assert "execute_agent" in container, "container must contain execute_agent use case"
    assert (
        "get_agent_info" in container
    ), "container must contain get_agent_info use case"


def test_container_with_extra_deps():
    from agent_sdk.bootstrap import build_app_container

    stub_logger = StubLogger()
    stub_monitor = StubMonitor()
    container = build_app_container(
        extra_dependencies={"logger": stub_logger, "monitor": stub_monitor}
    )
    execute_uc = container["execute_agent"]
    assert (
        execute_uc.logger is stub_logger
    ), "ExecuteAgentUseCase.logger must be the injected stub"
    assert (
        execute_uc.monitor is stub_monitor
    ), "ExecuteAgentUseCase.monitor must be the injected stub"


def test_container_with_graph():
    from agent_sdk.bootstrap import build_app_container

    class MockGraph:
        def invoke(self, state):
            return {
                **state,
                "formatted_response": {"content": "ok", "status": "success"},
            }

    mock_graph = MockGraph()
    container = build_app_container(extra_dependencies={"agent_graph": mock_graph})
    execute_uc = container["execute_agent"]
    assert (
        execute_uc.graph is mock_graph
    ), "ExecuteAgentUseCase.graph must be the injected mock_graph"


def test_build_app_container_graph_factory_receives_merged_worker_tools(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    mock_settings = MagicMock()
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.LLM_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.LLM_MODEL_KWARGS = {}
    mock_settings.MCP_SERVERS = [{"name": "math", "url": "http://localhost:9999/mcp"}]
    mock_settings.MCP_TOOL_FILTER = []

    monkeypatch.setattr("agent_sdk.bootstrap.settings", mock_settings)

    mcp_tool = MagicMock()
    mcp_tool.name = "mcp_add"
    local_tool = MagicMock()
    local_tool.name = "local_add"
    mcp_client = MagicMock()
    mcp_client.get_tools = AsyncMock(return_value=[mcp_tool])
    mcp_client.last_discovery_failures = []
    monkeypatch.setattr(
        "agent_sdk.bootstrap.MCPClientService", MagicMock(return_value=mcp_client)
    )

    compiled_graph = MagicMock(name="compiled_graph")
    captured: dict[str, object] = {}

    def graph_factory(deps):
        captured["worker_tools"] = deps["worker_tools"]
        return compiled_graph

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(
        agent_graph_factory=graph_factory,
        local_tools=[local_tool],
    )

    assert [tool.name for tool in captured["worker_tools"]] == ["local_add", "mcp_add"]
    assert container["execute_agent"].graph is compiled_graph


def test_build_app_container_wires_resume_agent_for_graph_factory():
    from unittest.mock import MagicMock

    from agent_sdk.bootstrap import build_app_container

    compiled_graph = MagicMock(name="compiled_graph")

    container = build_app_container(
        agent_graph_factory=lambda deps: compiled_graph,
    )

    assert container["execute_agent"].graph is compiled_graph
    assert container.get("resume_agent") is not None
    assert (
        container["message_reaction_router"]._resume_use_case
        is container["resume_agent"]
    )


def test_build_app_container_rejects_precompiled_graph_with_bootstrap_mcp_tools(
    monkeypatch,
):
    from unittest.mock import AsyncMock, MagicMock

    mock_settings = MagicMock()
    mock_settings.DEFAULT_TENANT_ID = "default"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.LLM_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.LLM_MODEL_KWARGS = {}
    mock_settings.MCP_SERVERS = [{"name": "math", "url": "http://localhost:9999/mcp"}]
    mock_settings.MCP_TOOL_FILTER = []

    monkeypatch.setattr("agent_sdk.bootstrap.settings", mock_settings)

    mcp_tool = MagicMock()
    mcp_tool.name = "mcp_add"
    mcp_client = MagicMock()
    mcp_client.get_tools = AsyncMock(return_value=[mcp_tool])
    mcp_client.last_discovery_failures = []
    monkeypatch.setattr(
        "agent_sdk.bootstrap.MCPClientService", MagicMock(return_value=mcp_client)
    )

    from agent_sdk.bootstrap import build_app_container

    with pytest.raises(ValueError, match="agent_graph_factory"):
        build_app_container(agent_graph=MagicMock(name="precompiled_graph"))


def test_scan_sdk_features():
    from agent_sdk.bootstrap import scan_and_load_features

    features = scan_and_load_features()
    assert (
        "execute_agent" in features
    ), "scan_and_load_features must discover execute_agent"
    assert (
        "get_agent_info" in features
    ), "scan_and_load_features must discover get_agent_info"
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentUseCase,
    )
    from agent_sdk.layer2_application.features.get_agent_info.use_cases.get_agent_info_use_case import (
        GetAgentInfoUseCase,
    )

    assert features["execute_agent"] is ExecuteAgentUseCase
    assert features["get_agent_info"] is GetAgentInfoUseCase


def test_container_auto_wires_mock_publisher_when_not_injected():
    """build_app_container must auto-inject a publisher when no extra_dependencies override."""
    import os

    os.environ.setdefault("APP_MODE", "SERVER")
    from agent_sdk.bootstrap import build_app_container
    from agent_sdk.layer4_frameworks.messaging.console_publisher import (
        ConsoleMessagePublisher,
    )

    container = build_app_container()
    deps = container["_dependencies"]
    assert "publisher" in deps, "publisher must be auto-wired into _dependencies"
    assert isinstance(deps["publisher"], ConsoleMessagePublisher)


def test_container_respects_injected_publisher_override():
    """extra_dependencies publisher must take precedence over auto-wiring."""
    from agent_sdk.bootstrap import build_app_container

    class _StubPublisher:
        async def publish(self, topic, message, key=None):
            pass

        async def close(self):
            pass

    stub = _StubPublisher()
    container = build_app_container(extra_dependencies={"publisher": stub})
    deps = container["_dependencies"]
    assert deps["publisher"] is stub


def test_container_respects_dependency_override_publisher():
    """dependency_overrides publisher must take precedence over auto-wiring."""
    from agent_sdk.bootstrap import build_app_container

    class _StubPublisher:
        async def publish(self, topic, message, key=None):
            pass

        async def close(self):
            pass

    stub = _StubPublisher()
    container = build_app_container(dependency_overrides={"publisher": stub})
    deps = container["_dependencies"]
    assert deps["publisher"] is stub


def test_container_server_mode_does_not_wire_consumer():
    """APP_MODE=SERVER must not inject a consumer (no background consumer loop)."""
    import os

    os.environ["APP_MODE"] = "SERVER"
    try:
        from agent_sdk.bootstrap import build_app_container

        container = build_app_container()
        deps = container["_dependencies"]
        assert "consumer" not in deps, "SERVER mode must not auto-wire a consumer"
    finally:
        os.environ.pop("APP_MODE", None)


def test_build_app_container_exposes_consumer_and_publisher_in_consumer_mode():
    from types import SimpleNamespace
    from unittest.mock import patch

    from agent_sdk.bootstrap import build_app_container

    settings = SimpleNamespace(
        DEFAULT_TENANT_ID="default",
        APP_MODE="CONSUMER",
        OPENAI_API_KEY="",
        LLM_MODEL="",
        MCP_SERVERS=[],
        MCP_TOOL_FILTER=[],
        REGISTRY_URL="",
        HANA_HOST="",
        AGENT_TYPE="test-agent",
        AGENT_VERSION="0.0.0",
    )

    with patch("agent_sdk.bootstrap.settings", settings):
        container = build_app_container()

    deps = container["_dependencies"]
    assert container["consumer"] is deps["consumer"]
    assert container["publisher"] is deps["publisher"]


def test_build_app_container_seeds_settings_in_dependencies():
    """build_app_container must always seed 'settings' into _dependencies."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    deps = container["_dependencies"]
    assert (
        "settings" in deps
    ), "build_app_container must seed 'settings' into _dependencies"


def test_build_app_container_seeds_default_tenant_id_in_dependencies():
    """build_app_container must always seed 'default_tenant_id' into _dependencies."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    deps = container["_dependencies"]
    assert (
        "default_tenant_id" in deps
    ), "build_app_container must seed 'default_tenant_id' into _dependencies"
    assert isinstance(deps["default_tenant_id"], str)


def test_build_app_container_wires_settings_into_get_agent_info_use_case():
    """build_app_container must wire settings into GetAgentInfoUseCase."""
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    get_agent_info_uc = container["get_agent_info"]
    assert (
        get_agent_info_uc._settings is not None
    ), "GetAgentInfoUseCase._settings must be wired from container"
    assert (
        not hasattr(get_agent_info_uc._settings, "__class__")
        or get_agent_info_uc._settings is not None
    )


def test_build_app_container_wires_correlation_thread_store_into_delegator_and_router():
    from agent_sdk.bootstrap import build_app_container

    class _StubPublisher:
        async def publish(self, topic, message, key=None):
            pass

        async def close(self):
            pass

    class _StubConsumer:
        async def start(self, handler):
            return None

        async def stop(self):
            return None

    class _StubRegistry:
        async def list_capabilities(self):
            return []

    class _StubSharedStateRepository:
        def load(self, key: str):
            return None

        def get(self, key: str):
            return None

        def save(self, record):
            return record

        def compare_and_set(self, key: str, state: dict, expected_version: int):
            return None

        def acquire_lock(self, key: str, owner: str, ttl_seconds: int):
            return None

        def release_lock(self, key: str, owner: str) -> bool:
            return True

        def delete(self, key: str) -> bool:
            return True

    container = build_app_container(
        extra_dependencies={
            "publisher": _StubPublisher(),
            "consumer": _StubConsumer(),
            "agent_registry": _StubRegistry(),
            "shared_state_repository": _StubSharedStateRepository(),
        }
    )

    deps = container["_dependencies"]
    store = deps["correlation_thread_store"]
    assert container["message_reaction_router"]._correlation_thread_store is store
    assert deps["agent_delegator"]._correlation_thread_store is store


def test_build_app_container_uses_override_settings_for_correlation_thread_store():
    from types import SimpleNamespace

    from agent_sdk.bootstrap import build_app_container

    runtime_settings = SimpleNamespace(
        DEFAULT_TENANT_ID="default",
        APP_MODE="SERVER",
        LLM_MODEL="",
        MCP_SERVERS=[],
        MCP_TOOL_FILTER=[],
        AGENT_CALL_CORRELATION_TTL_SECONDS=12,
        AGENT_CALL_CORRELATION_MAX_ENTRIES=34,
    )

    container = build_app_container(dependency_overrides={"settings": runtime_settings})

    store = container["_dependencies"]["correlation_thread_store"]
    assert store._ttl_seconds == 12
    assert store._max_entries == 34
