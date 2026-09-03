"""Tests for RemoteAgentTool.

Covers:
- RemoteAgentTool is importable from services module
- RemoteAgentTool inherits from BaseTool
- Constructor accepts remote_agent_type, registry, name, description
- _run raises NotImplementedError (async-only tool)
- _arun resolves agent_id via registry and calls interrupt
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool


def test_remote_agent_tool_is_importable():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    assert RemoteAgentTool is not None


def test_remote_agent_tool_inherits_base_tool():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    assert issubclass(RemoteAgentTool, BaseTool)


def test_remote_agent_tool_construction():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    tool = RemoteAgentTool(
        remote_agent_type="agent-type-x",
        registry=registry,
        name="test_agent",
        description="A test remote agent",
    )
    assert tool.remote_agent_type == "agent-type-x"
    assert tool.name == "test_agent"
    assert tool.description == "A test remote agent"
    assert tool._registry is registry
    assert tool._resolved_id is None


def test_remote_agent_tool_run():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    tool = RemoteAgentTool(
        remote_agent_type="agent-type-x",
        registry=registry,
        name="test_agent",
        description="A test remote agent",
    )
    with pytest.raises(NotImplementedError):
        tool._run(input="test")


@pytest.mark.asyncio
async def test_remote_agent_tool_arun():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    registry.resolve_agent_id = AsyncMock(return_value="uuid-123")
    tool = RemoteAgentTool(
        remote_agent_type="my-agent", registry=registry, name="n", description="d"
    )
    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt"
    ) as mock_interrupt:
        mock_interrupt.return_value = "result"
        result = await tool._arun(input="hello")
    registry.resolve_agent_id.assert_awaited_once_with("my-agent")
    mock_interrupt.assert_called_once_with(
        {
            "type": "AGENT_CALL",
            "agent_id": "uuid-123",
            "agent_type": "my-agent",
            "input": "hello",
            "message": "Calling agent my-agent (uuid-123)",
        }
    )
    assert result == "result"


def test_remote_agent_tool_can_invalidate_cached_resolution():
    from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool

    registry = MagicMock()
    tool = RemoteAgentTool(
        remote_agent_type="agent-type-x",
        registry=registry,
        name="test_agent",
        description="A test remote agent",
    )

    tool._resolved_id = "stale-id"
    tool._resolved_at = 123.0

    tool.invalidate_cached_resolution()

    assert tool._resolved_id is None
    assert tool._resolved_at == 0.0
