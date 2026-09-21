from __future__ import annotations

import logging

from agent_sdk.layer4_frameworks.logging.standard import StandardLogger
from agent_sdk.layer4_frameworks.mcp.mcp_client_service import MCPClientService


def test_mcp_client_can_log_missing_server_url_with_standard_logger(caplog) -> None:
    service = MCPClientService(
        server_configs=[{"name": "missing-url"}],
        logger=StandardLogger(),
    )

    with caplog.at_level(logging.WARNING, logger="AgentSDK"):
        config = service._build_client_config({"name": "missing-url"})

    assert config is None
    assert "MCP server 'missing-url' has no URL; skipping." in caplog.text
