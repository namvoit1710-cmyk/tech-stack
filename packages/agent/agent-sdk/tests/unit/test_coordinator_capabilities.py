from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability
from agent_sdk.layer4_frameworks.http.agent_call_coordinator import (
    AgentCallCoordinator,
)


def _make_capability(**overrides) -> AgentCapability:
    defaults = dict(
        agent_type="file-processor",
        name="File Processor",
        description="Processes files",
        input_schema={"type": "object", "properties": {}},
        output_schema={},
        timeout_seconds=60.0,
        required_parameters=["file_id"],
    )
    defaults.update(overrides)
    return AgentCapability(**defaults)


@pytest.fixture
def coordinator_with_caps():
    cap = _make_capability()
    resolver = MagicMock()
    resolver.resolve_endpoint = AsyncMock(return_value="http://agent:9000")
    resume_uc = MagicMock()
    http_client = AsyncMock()
    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=resume_uc,
        http_client=http_client,
        capabilities={"file-processor": cap},
    )
    return coordinator, http_client, resolver


@pytest.mark.asyncio
async def test_uses_per_agent_timeout(coordinator_with_caps):
    """Coordinator uses capability.timeout_seconds instead of flat default."""
    coordinator, http_client, _ = coordinator_with_caps
    http_client.post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {"result": "ok"},
            raise_for_status=lambda: None,
        )
    )
    call = AgentCallRequest(
        agent_id="a-1",
        agent_type="file-processor",
        input_payload={"file_id": "f-1"},
        interrupt_id="i-1",
        thread_id="t-1",
    )
    await coordinator._call_sub_agent(call)
    _, kwargs = http_client.post.call_args
    assert kwargs["timeout"] == 60.0  # from capability, not default 300


@pytest.mark.asyncio
async def test_validates_required_parameters(coordinator_with_caps):
    """Coordinator returns error when required parameters are missing."""
    coordinator, http_client, _ = coordinator_with_caps
    call = AgentCallRequest(
        agent_id="a-1",
        agent_type="file-processor",
        input_payload={"instructions": "summarize"},  # missing file_id
        interrupt_id="i-1",
        thread_id="t-1",
    )
    result = await coordinator._call_sub_agent(call)
    assert result["success"] is False
    assert "file_id" in result["error"]
    # HTTP client should NOT have been called
    http_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_falls_back_to_default_timeout_without_capabilities():
    """Without capabilities, coordinator uses its default timeout."""
    resolver = MagicMock()
    resolver.resolve_endpoint = AsyncMock(return_value="http://agent:9000")
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {"result": "ok"},
            raise_for_status=lambda: None,
        )
    )
    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=MagicMock(),
        http_client=http_client,
        timeout=300.0,
        # no capabilities parameter
    )
    call = AgentCallRequest(
        agent_id="a-1",
        agent_type="file-processor",
        input_payload={"file_id": "f-1"},
        interrupt_id="i-1",
        thread_id="t-1",
    )
    await coordinator._call_sub_agent(call)
    _, kwargs = http_client.post.call_args
    assert kwargs["timeout"] == 300.0


@pytest.mark.asyncio
async def test_validation_passes_when_all_params_present(coordinator_with_caps):
    """Coordinator proceeds normally when all required params are present."""
    coordinator, http_client, _ = coordinator_with_caps
    http_client.post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {"result": "ok"},
            raise_for_status=lambda: None,
        )
    )
    call = AgentCallRequest(
        agent_id="a-1",
        agent_type="file-processor",
        input_payload={"file_id": "f-1", "instructions": "summarize"},
        interrupt_id="i-1",
        thread_id="t-1",
    )
    result = await coordinator._call_sub_agent(call)
    assert result == {"result": "ok"}
    http_client.post.assert_called_once()
