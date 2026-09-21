from unittest.mock import AsyncMock, MagicMock, patch

from agent_sdk.layer2_application.features.get_agent_info.use_cases.get_agent_info_use_case import (
    GetAgentInfoUseCase,
)


def _make_stub_settings(**kwargs):
    s = MagicMock()
    s.AGENT_TYPE = kwargs.get("AGENT_TYPE", "tool-agent")
    s.AGENT_VERSION = kwargs.get("AGENT_VERSION", "1.0")
    s.SDK_VERSION = kwargs.get("SDK_VERSION", "2.0.0")
    s.AGENT_DOMAIN = kwargs.get("AGENT_DOMAIN", "testing")
    s.DEFAULT_TENANT_ID = kwargs.get("DEFAULT_TENANT_ID", "default")
    s.CAPABILITIES = kwargs.get("CAPABILITIES", [])
    return s


def _make_stub_logger():
    logger = MagicMock()
    logger.info = MagicMock()
    return logger


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_skips_tools_when_no_local_tools(mock_settings):
    mock_settings.APP_NAME = "test-agent"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.AGENT_TYPE = "test"
    mock_settings.REGISTRY_URL = ""
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 30
    mock_settings.SERVER_PORT = 36000
    mock_settings.SERVER_HOST = "0.0.0.0"
    mock_settings.LLM_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []
    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    tools = container["_dependencies"].get("worker_tools", [])
    assert tools == []


@patch("agent_sdk.bootstrap.settings")
def test_build_app_container_merges_local_tools(mock_settings):
    """build_app_container must expose local_tools as worker_tools dependency."""
    mock_settings.APP_NAME = "test-agent"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.AGENT_TYPE = "test"
    mock_settings.REGISTRY_URL = ""
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 30
    mock_settings.SERVER_PORT = 36000
    mock_settings.SERVER_HOST = "0.0.0.0"
    mock_settings.LLM_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []

    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def local_add(x: int) -> int:
        """Add one to x."""
        return x + 1

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(local_tools=[local_add])
    worker_tools = container["_dependencies"]["worker_tools"]
    assert len(worker_tools) == 1
    assert worker_tools[0].name == "local_add"


@patch("agent_sdk.bootstrap.settings")
def test_build_app_container_keeps_local_worker_tools_and_registry_ids_separate(
    mock_settings,
):
    mock_settings.APP_NAME = "test-agent"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.AGENT_TYPE = "planner"
    mock_settings.AGENT_VERSION = "1.2.3"
    mock_settings.REGISTRY_URL = "http://registry"
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 30
    mock_settings.SERVER_PORT = 36000
    mock_settings.SERVER_HOST = "0.0.0.0"
    mock_settings.LLM_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []

    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def local_add(x: int) -> int:
        """Add one to x."""
        return x + 1

    tool_registry = MagicMock()
    tool_registry.sync_tools = AsyncMock(return_value={"local_add": "tool-123"})

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container(
        local_tools=[local_add],
        extra_dependencies={"tool_registry": tool_registry},
    )

    dependencies = container["_dependencies"]
    assert [tool.name for tool in dependencies["local_worker_tools"]] == ["local_add"]
    assert [tool.name for tool in dependencies["worker_tools"]] == ["local_add"]
    assert dependencies["registered_tool_ids"] == {"local_add": "tool-123"}
    tool_registry.sync_tools.assert_awaited_once()


@patch("agent_sdk.bootstrap.settings")
def test_build_app_container_registers_local_tools_through_tool_registrar(
    mock_settings,
):
    mock_settings.APP_NAME = "test-agent"
    mock_settings.APP_MODE = "SERVER"
    mock_settings.AGENT_TYPE = "planner"
    mock_settings.AGENT_VERSION = "1.2.3"
    mock_settings.REGISTRY_URL = "http://registry"
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 30
    mock_settings.SERVER_PORT = 36000
    mock_settings.SERVER_HOST = "0.0.0.0"
    mock_settings.LLM_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    mock_settings.LLM_TEMPERATURE = 0.01
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = 6
    mock_settings.MCP_SERVERS = []
    mock_settings.MCP_TOOL_FILTER = []

    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def local_add(x: int) -> int:
        """Add one to x."""
        return x + 1

    tool_registry = MagicMock()

    registrar = MagicMock()
    registrar.register_tools = AsyncMock(return_value={"local_add": "tool-123"})

    with patch("agent_sdk.bootstrap.ToolRegistrar", create=True) as mock_registrar_cls:
        mock_registrar_cls.return_value = registrar

        from agent_sdk.bootstrap import build_app_container

        container = build_app_container(
            local_tools=[local_add],
            extra_dependencies={"tool_registry": tool_registry},
        )

    dependencies = container["_dependencies"]
    assert dependencies["registered_tool_ids"] == {"local_add": "tool-123"}
    mock_registrar_cls.assert_called_once_with(tool_registry)
    registrar.register_tools.assert_awaited_once_with(
        [local_add],
        owner_kind="agent",
        owner_name="planner",
        owner_version="1.2.3",
    )


def test_get_agent_info_use_case_execute_returns_tenant_aware_metadata():
    """execute() must include tenant_aware=True in AgentInfo.metadata."""
    settings = _make_stub_settings(DEFAULT_TENANT_ID="acme")
    uc = GetAgentInfoUseCase(logger=_make_stub_logger(), settings=settings)
    info = uc.execute()
    assert (
        "tenant_aware" in info.metadata
    ), "AgentInfo.metadata must contain 'tenant_aware' key"
    assert (
        info.metadata["tenant_aware"] is True
    ), "AgentInfo.metadata['tenant_aware'] must be True"


def test_get_agent_info_use_case_execute_returns_default_tenant_id_in_metadata():
    """execute() must include default_tenant_id in AgentInfo.metadata from settings."""
    settings = _make_stub_settings(DEFAULT_TENANT_ID="my-tenant")
    uc = GetAgentInfoUseCase(logger=_make_stub_logger(), settings=settings)
    info = uc.execute()
    assert (
        "default_tenant_id" in info.metadata
    ), "AgentInfo.metadata must contain 'default_tenant_id' key"
    assert (
        info.metadata["default_tenant_id"] == "my-tenant"
    ), f"AgentInfo.metadata['default_tenant_id'] must equal 'my-tenant', got {info.metadata['default_tenant_id']}"
