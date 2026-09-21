"""Tests for RemoteAgentTool registry-based resolution.

Covers:
- Successful agent_id resolution via registry
- Resolution failure raises ValueError
- Sync _run raises NotImplementedError
- _resolve caches the agent_id (registry called only once across two _arun calls)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_resolution_success():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(return_value="uuid-abc")
    tool = RemoteAgentTool(
        remote_agent_type="my-agent", registry=registry, name="n", description="d"
    )
    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt"
    ) as mock_interrupt:
        mock_interrupt.return_value = "ok"
        await tool._arun(input="hello")
    registry.resolve_agent_id.assert_awaited_once_with("my-agent")


@pytest.mark.asyncio
async def test_resolution_failure_raises_value_error():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(return_value=None)
    tool = RemoteAgentTool(
        remote_agent_type="unknown-agent", registry=registry, name="n", description="d"
    )
    with pytest.raises(
        ValueError, match="Could not resolve agent_id for type: unknown-agent"
    ):
        await tool._arun(input="test")


def test_sync_run_raises_not_implemented():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    tool = RemoteAgentTool(
        remote_agent_type="my-agent", registry=registry, name="n", description="d"
    )
    with pytest.raises(NotImplementedError):
        tool._run(input="test")


@pytest.mark.asyncio
async def test_resolve_caches_agent_id():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(return_value="uuid-cached")
    tool = RemoteAgentTool(
        remote_agent_type="cached-agent", registry=registry, name="n", description="d"
    )
    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt"
    ) as mock_interrupt:
        mock_interrupt.return_value = "result"
        await tool._arun(input="first call")
        await tool._arun(input="second call")
    assert registry.resolve_agent_id.call_count == 1
