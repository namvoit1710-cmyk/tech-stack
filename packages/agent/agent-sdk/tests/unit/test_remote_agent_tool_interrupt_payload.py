from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool


@pytest.fixture
def mock_registry():
    reg = MagicMock()
    reg.resolve_agent_id = AsyncMock(return_value="agent-uuid-123")
    return reg


@pytest.mark.asyncio
async def test_interrupt_payload_includes_agent_type(mock_registry, monkeypatch):
    """RemoteAgentTool._arun must include agent_type in the interrupt payload."""
    tool = RemoteAgentTool(
        remote_agent_type="file-processor",
        registry=mock_registry,
        name="call_file_processor",
        description="Process files",
    )
    captured = {}

    def fake_interrupt(value):
        captured["value"] = value
        return value

    monkeypatch.setattr(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt",
        fake_interrupt,
    )
    await tool._arun(input='{"file_id": "f-1"}')
    assert captured["value"]["agent_type"] == "file-processor"
    assert captured["value"]["type"] == "AGENT_CALL"
    assert captured["value"]["agent_id"] == "agent-uuid-123"


@pytest.mark.asyncio
async def test_interrupt_payload_includes_async_delegation_correlation_fields(
    mock_registry, monkeypatch
):
    tool = RemoteAgentTool(
        remote_agent_type="file-processor",
        registry=mock_registry,
        name="call_file_processor",
        description="Process files",
    )
    captured = {}

    def fake_interrupt(value):
        captured["value"] = value
        return value

    monkeypatch.setattr(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt",
        fake_interrupt,
    )
    await tool._arun(
        input={"file_id": "f-1"},
        correlation_id="corr-123",
        reply_queue="parent.reply",
        session_id="sess-1",
        context_snapshot={"shared_state": {"k": "v"}},
    )

    assert captured["value"]["correlation_id"] == "corr-123"
    assert captured["value"]["reply_queue"] == "parent.reply"
    assert captured["value"]["session_id"] == "sess-1"
    assert captured["value"]["context_snapshot"] == {"shared_state": {"k": "v"}}
