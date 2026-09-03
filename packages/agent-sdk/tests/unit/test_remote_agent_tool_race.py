import asyncio
from unittest.mock import MagicMock

import pytest

from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool


@pytest.mark.asyncio
async def test_concurrent_resolve_returns_same_id():
    """Two concurrent _resolve() calls must return the same agent_id and only call registry once."""
    call_count = 0

    async def slow_resolve(agent_type):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0)
        return "agent-123"

    mock_registry = MagicMock()
    mock_registry.resolve_agent_id = slow_resolve

    tool = RemoteAgentTool(
        remote_agent_type="test",
        registry=mock_registry,
        name="test_tool",
        description="test",
    )
    results = await asyncio.gather(tool._resolve(), tool._resolve())

    assert results[0] == results[1] == "agent-123"
    assert call_count == 1, f"Registry called {call_count} times, expected 1"
