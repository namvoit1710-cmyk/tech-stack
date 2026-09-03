from __future__ import annotations

import logging

from agent_sdk.layer4_frameworks.logging.standard import StandardLogger


def test_standard_logger_accepts_sdk_metadata_kwargs(caplog) -> None:
    logger = StandardLogger()

    with caplog.at_level(logging.INFO, logger="AgentSDK"):
        logger.info("MCP discovery started", server_name="demo")

    assert "MCP discovery started" in caplog.text
    assert "server_name" in caplog.text
    assert "demo" in caplog.text


def test_standard_logger_accepts_stdlib_positional_format_args(caplog) -> None:
    logger = StandardLogger()

    with caplog.at_level(logging.WARNING, logger="AgentSDK"):
        logger.warning("MCP server '%s' has no URL; skipping.", "demo")

    assert "MCP server 'demo' has no URL; skipping." in caplog.text


def test_standard_logger_accepts_stdlib_exc_info_keyword(caplog) -> None:
    logger = StandardLogger()

    with caplog.at_level(logging.ERROR, logger="AgentSDK"):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.error("Failed to get MCP tools: %s", "boom", exc_info=True)

    assert "Failed to get MCP tools: boom" in caplog.text
    assert "Traceback" in caplog.text
