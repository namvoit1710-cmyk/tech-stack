import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_resolved_id_expires_after_ttl():
    """After TTL expires, _resolve should call registry again."""
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(side_effect=["uuid-1", "uuid-2"])

    tool = RemoteAgentTool(
        remote_agent_type="my-agent",
        registry=registry,
        name="n",
        description="d",
        resolve_ttl_seconds=0.1,  # 100ms TTL for testing
    )

    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt"
    ) as mock_interrupt:
        mock_interrupt.return_value = "ok"
        await tool._arun(input="first")
        assert registry.resolve_agent_id.call_count == 1

        # Wait for TTL to expire
        await asyncio.sleep(0.15)

        await tool._arun(input="second")
        assert registry.resolve_agent_id.call_count == 2


@pytest.mark.asyncio
async def test_resolved_id_cached_within_ttl():
    """Within TTL, _resolve should use cached value."""
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(return_value="uuid-cached")

    tool = RemoteAgentTool(
        remote_agent_type="my-agent",
        registry=registry,
        name="n",
        description="d",
        resolve_ttl_seconds=60.0,  # Long TTL
    )

    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt"
    ) as mock_interrupt:
        mock_interrupt.return_value = "ok"
        await tool._arun(input="first")
        await tool._arun(input="second")
        assert registry.resolve_agent_id.call_count == 1


@pytest.mark.asyncio
async def test_default_ttl_is_zero_for_backward_compat():
    """Default TTL of 0 means cache forever (backward compatible)."""
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(return_value="uuid-forever")

    tool = RemoteAgentTool(
        remote_agent_type="my-agent",
        registry=registry,
        name="n",
        description="d",
        # No resolve_ttl_seconds — should default to 0 (cache forever)
    )

    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt"
    ) as mock_interrupt:
        mock_interrupt.return_value = "ok"
        await tool._arun(input="first")
        await tool._arun(input="second")
        assert registry.resolve_agent_id.call_count == 1
