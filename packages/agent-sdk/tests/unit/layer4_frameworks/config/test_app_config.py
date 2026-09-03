import json
import os
from unittest.mock import patch

from agent_sdk.layer4_frameworks.config.app_config import Settings


def test_mcp_servers_default_empty():
    """MCP_SERVERS defaults to empty list when not set."""
    with patch.dict(os.environ, {}, clear=False):
        s = Settings()
        assert s.MCP_SERVERS == []
        assert s.MCP_TOOL_FILTER == []


def test_mcp_servers_from_env():
    """MCP_SERVERS parses JSON list from environment."""
    servers = [{"name": "math", "url": "http://localhost:8080/mcp"}]
    with patch.dict(os.environ, {"MCP_SERVERS": json.dumps(servers)}):
        s = Settings()
        assert len(s.MCP_SERVERS) == 1
        assert s.MCP_SERVERS[0]["name"] == "math"


def test_mcp_tool_filter_from_env():
    """MCP_TOOL_FILTER parses JSON list from environment."""
    with patch.dict(os.environ, {"MCP_TOOL_FILTER": '["add", "multiply"]'}):
        s = Settings()
        assert s.MCP_TOOL_FILTER == ["add", "multiply"]
