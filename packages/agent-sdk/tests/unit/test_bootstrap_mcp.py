"""Tests for MCP wiring in bootstrap."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_no_mcp_servers(mock_settings_obj):
    """When MCP_SERVERS is empty, no MCP tools are added."""
    mock_settings_obj.DEFAULT_TENANT_ID = "default"
    mock_settings_obj.APP_MODE = "SERVER"
    mock_settings_obj.LLM_MODEL = ""
    mock_settings_obj.LLM_PROVIDER = ""
    mock_settings_obj.LLM_TEMPERATURE = 0.01
    mock_settings_obj.LLM_TIMEOUT = None
    mock_settings_obj.LLM_MAX_TOKENS = None
    mock_settings_obj.LLM_MAX_RETRIES = 6
    mock_settings_obj.MCP_SERVERS = []
    mock_settings_obj.MCP_TOOL_FILTER = []

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()
    assert container is not None
    worker_tools = container["_dependencies"]["worker_tools"]
    assert worker_tools == [], f"Expected no MCP tools, got: {worker_tools}"


@patch("agent_sdk.bootstrap.MCPClientService")
@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_with_mcp_servers(mock_settings_obj, MockMCPService):
    """When MCP_SERVERS is set, MCP client is created and tools are merged into worker_tools."""
    mock_settings_obj.DEFAULT_TENANT_ID = "default"
    mock_settings_obj.APP_MODE = "SERVER"
    mock_settings_obj.LLM_MODEL = ""
    mock_settings_obj.LLM_PROVIDER = ""
    mock_settings_obj.LLM_TEMPERATURE = 0.01
    mock_settings_obj.LLM_TIMEOUT = None
    mock_settings_obj.LLM_MAX_TOKENS = None
    mock_settings_obj.LLM_MAX_RETRIES = 6
    mock_settings_obj.MCP_SERVERS = [
        {"name": "math", "url": "http://localhost:9999/mcp"}
    ]
    mock_settings_obj.MCP_TOOL_FILTER = []

    mock_tool = MagicMock()
    mock_tool.name = "add"
    mock_instance = MagicMock()
    mock_instance.get_tools = AsyncMock(return_value=[mock_tool])
    MockMCPService.return_value = mock_instance

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()

    # Verify MCPClientService was constructed
    MockMCPService.assert_called_once()
    # Verify MCP tools were merged into worker_tools
    worker_tools = container["_dependencies"]["worker_tools"]
    assert mock_tool in worker_tools, "MCP tool should be in worker_tools"


@patch("agent_sdk.bootstrap.MCPClientService")
@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_with_mcp_tool_filter(mock_settings_obj, MockMCPService):
    """When MCP_TOOL_FILTER is set, only filtered tools are merged."""
    mock_settings_obj.DEFAULT_TENANT_ID = "default"
    mock_settings_obj.APP_MODE = "SERVER"
    mock_settings_obj.LLM_MODEL = ""
    mock_settings_obj.LLM_PROVIDER = ""
    mock_settings_obj.LLM_TEMPERATURE = 0.01
    mock_settings_obj.LLM_TIMEOUT = None
    mock_settings_obj.LLM_MAX_TOKENS = None
    mock_settings_obj.LLM_MAX_RETRIES = 6
    mock_settings_obj.MCP_SERVERS = [
        {"name": "math", "url": "http://localhost:9999/mcp"}
    ]
    mock_settings_obj.MCP_TOOL_FILTER = ["add"]

    mock_tool = MagicMock()
    mock_tool.name = "add"
    mock_instance = MagicMock()
    mock_instance.get_filtered_tools = AsyncMock(return_value=[mock_tool])
    MockMCPService.return_value = mock_instance

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()

    MockMCPService.assert_called_once()
    worker_tools = container["_dependencies"]["worker_tools"]
    assert mock_tool in worker_tools, "Filtered MCP tool should be in worker_tools"


@patch("agent_sdk.bootstrap.MCPClientService")
@patch("agent_sdk.bootstrap.logger")
@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_logs_mcp_load_failure_with_context(
    mock_settings_obj, mock_logger, MockMCPService
):
    """When MCP tool loading raises repeatedly, bootstrap logs retries and a final traceback-bearing warning."""
    mock_settings_obj.DEFAULT_TENANT_ID = "default"
    mock_settings_obj.APP_MODE = "SERVER"
    mock_settings_obj.LLM_MODEL = ""
    mock_settings_obj.LLM_PROVIDER = ""
    mock_settings_obj.LLM_TEMPERATURE = 0.01
    mock_settings_obj.LLM_TIMEOUT = None
    mock_settings_obj.LLM_MAX_TOKENS = None
    mock_settings_obj.LLM_MAX_RETRIES = 6
    mock_settings_obj.MCP_SERVERS = [
        {"name": "math", "url": "http://localhost:9999/mcp"}
    ]
    mock_settings_obj.MCP_TOOL_FILTER = []

    mock_instance = MagicMock()
    mock_instance.get_tools = AsyncMock(side_effect=ValueError("connection refused"))
    MockMCPService.return_value = mock_instance

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()

    assert container is not None
    assert mock_logger.warning.call_count == 3
    _, kwargs = mock_logger.warning.call_args_list[-1]
    assert (
        kwargs.get("exc_info") is True
    ), "logger.warning must be called with exc_info=True to preserve traceback"


@patch("agent_sdk.bootstrap.time.sleep")
@patch("agent_sdk.bootstrap.MCPClientService")
@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_retries_mcp_load_until_success(
    mock_settings_obj, MockMCPService, mock_sleep
):
    """Transient MCP discovery failures should be retried with backoff before tools are merged."""
    mock_settings_obj.DEFAULT_TENANT_ID = "default"
    mock_settings_obj.APP_MODE = "SERVER"
    mock_settings_obj.LLM_MODEL = ""
    mock_settings_obj.LLM_PROVIDER = ""
    mock_settings_obj.LLM_TEMPERATURE = 0.01
    mock_settings_obj.LLM_TIMEOUT = None
    mock_settings_obj.LLM_MAX_TOKENS = None
    mock_settings_obj.LLM_MAX_RETRIES = 6
    mock_settings_obj.MCP_SERVERS = [
        {"name": "math", "url": "http://localhost:9999/mcp"}
    ]
    mock_settings_obj.MCP_TOOL_FILTER = []

    mock_tool = MagicMock()
    mock_tool.name = "add"
    mock_instance = MagicMock()
    attempts = {"count": 0}

    async def get_tools():
        attempts["count"] += 1
        mock_instance.last_discovery_failures = (
            ["math"] if attempts["count"] == 1 else []
        )
        return [] if attempts["count"] == 1 else [mock_tool]

    mock_instance.get_tools = AsyncMock(side_effect=get_tools)
    MockMCPService.return_value = mock_instance

    from agent_sdk.bootstrap import build_app_container

    container = build_app_container()

    assert attempts["count"] == 2
    mock_sleep.assert_called_once()
    assert container["_dependencies"]["mcp_client"] is mock_instance
    assert mock_tool in container["_dependencies"]["worker_tools"]


@patch("agent_sdk.bootstrap.MCPClientService")
@patch("agent_sdk.bootstrap.settings")
def test_bootstrap_raises_clear_runtime_error_when_called_in_running_loop(
    mock_settings_obj, MockMCPService
):
    """The sync bootstrap must fail clearly when MCP loading is attempted inside a running event loop."""
    mock_settings_obj.DEFAULT_TENANT_ID = "default"
    mock_settings_obj.APP_MODE = "SERVER"
    mock_settings_obj.LLM_MODEL = ""
    mock_settings_obj.LLM_PROVIDER = ""
    mock_settings_obj.LLM_TEMPERATURE = 0.01
    mock_settings_obj.LLM_TIMEOUT = None
    mock_settings_obj.LLM_MAX_TOKENS = None
    mock_settings_obj.LLM_MAX_RETRIES = 6
    mock_settings_obj.MCP_SERVERS = [
        {"name": "math", "url": "http://localhost:9999/mcp"}
    ]
    mock_settings_obj.MCP_TOOL_FILTER = []

    mock_instance = MagicMock()
    mock_instance.get_tools = AsyncMock(return_value=[])
    MockMCPService.return_value = mock_instance

    from agent_sdk.bootstrap import build_app_container

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="running event loop"):
            build_app_container()

    asyncio.run(_run())
