"""Tests for tenant_context propagation in ExecuteAgentUseCase.

Covers:
- _execute_via_graph populates tenant_context in initial graph state from AgentRequest fields.
- DEFAULT_TENANT_ID fallback when tenant_id is empty on the request.
- Raw transport fields (conv_id, user_id, tenant_id) are preserved alongside tenant_context.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentUseCase,
)


def _make_use_case(captured_states: list, default_tenant_id: str = "global-default"):
    """Build an ExecuteAgentUseCase with a mock graph that captures initial state."""
    logger = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    logger.warning = MagicMock()

    monitor = MagicMock()
    monitor.track = MagicMock()

    async def _ainvoke(state, config=None):
        captured_states.append(dict(state))
        return {"message": "ok", "transport_state": "COMPLETED"}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=_ainvoke)

    return ExecuteAgentUseCase(
        logger=logger,
        monitor=monitor,
        agent_graph=mock_graph,
        default_tenant_id=default_tenant_id,
    )


@pytest.mark.asyncio
async def test_execute_via_graph_populates_tenant_context():
    """_execute_via_graph must include a JSON-safe tenant_context in initial graph state."""
    captured = []
    use_case = _make_use_case(captured)

    request = ExecuteAgentInput(
        message="hello",
        conv_id="conv-123",
        user_id="user-abc",
        tenant_id="tenant-xyz",
        source="api",
        correlation_id="corr-001",
    )
    await use_case.execute(request)

    assert len(captured) == 1, "Graph should have been invoked once"
    state = captured[0]
    assert "tenant_context" in state, "Initial graph state must include tenant_context"
    ctx = state["tenant_context"]
    assert ctx == {
        "tenant_id": "tenant-xyz",
        "user_id": "user-abc",
        "conv_id": "conv-123",
        "source": "api",
        "correlation_id": "corr-001",
        "metadata": {},
    }


@pytest.mark.asyncio
async def test_execute_via_graph_preserves_raw_request_fields():
    """Raw request fields (conv_id, user_id, tenant_id) must still be in initial state."""
    captured = []
    use_case = _make_use_case(captured)

    request = ExecuteAgentInput(
        message="test message",
        conv_id="conv-456",
        user_id="user-def",
        tenant_id="tenant-abc",
    )
    await use_case.execute(request)

    state = captured[0]
    assert state["conv_id"] == "conv-456"
    assert state["user_id"] == "user-def"
    assert state["tenant_id"] == "tenant-abc"
    assert state["message"] == "test message"


@pytest.mark.asyncio
async def test_execute_via_graph_uses_default_tenant_id_when_empty():
    """tenant_context.tenant_id should fall back to default_tenant_id when request.tenant_id is empty."""
    captured = []
    use_case = _make_use_case(captured, default_tenant_id="global-default")

    request = ExecuteAgentInput(
        message="test",
        conv_id="conv-789",
        user_id="user-ghi",
        tenant_id="",  # empty — should fall back to default_tenant_id
    )
    await use_case.execute(request)

    state = captured[0]
    ctx = state["tenant_context"]
    assert (
        ctx["tenant_id"] == "global-default"
    ), f"tenant_context.tenant_id should fall back to 'global-default', got '{ctx['tenant_id']}'"


@pytest.mark.asyncio
async def test_execute_via_graph_uses_default_tenant_id_when_whitespace():
    """tenant_context.tenant_id should fall back to default_tenant_id when request.tenant_id is whitespace."""
    captured = []
    use_case = _make_use_case(captured, default_tenant_id="fallback-tenant")

    request = ExecuteAgentInput(
        message="test",
        conv_id="conv-000",
        user_id="user-jkl",
        tenant_id="   ",  # whitespace — should also fall back
    )
    await use_case.execute(request)

    ctx = captured[0]["tenant_context"]
    assert ctx["tenant_id"] == "fallback-tenant"


@pytest.mark.asyncio
async def test_agent_request_source_is_primary_for_tenant_context():
    """AgentRequest.source (not context.source) is used to build tenant_context."""
    captured = []
    use_case = _make_use_case(captured)

    from agent_sdk.layer1_domain.entities.agent_request import RequestContext

    request = ExecuteAgentInput(
        message="hello",
        conv_id="conv-src",
        user_id="user-src",
        tenant_id="tenant-src",
        source="kafka",
        context=RequestContext(agent="bot", source="websocket"),
    )
    await use_case.execute(request)

    state = captured[0]
    ctx = state["tenant_context"]
    assert (
        ctx["source"] == "kafka"
    ), f"tenant_context.source must use AgentRequest.source ('kafka'), got '{ctx['source']}'"


@pytest.mark.asyncio
async def test_agent_request_source_is_primary_in_initial_state():
    """AgentRequest.source (not context.source) is placed into initial graph state."""
    captured = []
    use_case = _make_use_case(captured)

    from agent_sdk.layer1_domain.entities.agent_request import RequestContext

    request = ExecuteAgentInput(
        message="hello",
        conv_id="conv-src2",
        user_id="user-src2",
        tenant_id="tenant-src2",
        source="kafka",
        context=RequestContext(agent="bot", source="websocket"),
    )
    await use_case.execute(request)

    state = captured[0]
    assert (
        state["source"] == "kafka"
    ), f"initial_state['source'] must use AgentRequest.source ('kafka'), got '{state['source']}'"


@pytest.mark.asyncio
async def test_execute_via_graph_uses_stripped_tenant_id_in_initial_state():
    """initial_state['tenant_id'] must use the stripped local variable, not request.tenant_id."""
    captured = []
    use_case = _make_use_case(captured, default_tenant_id="default-tenant")

    request = ExecuteAgentInput(
        message="test",
        conv_id="conv-strip",
        user_id="user-strip",
        tenant_id="  tenant-padded  ",
    )
    await use_case.execute(request)

    state = captured[0]
    assert (
        state["tenant_id"] == "tenant-padded"
    ), f"initial_state['tenant_id'] must be stripped, got '{state['tenant_id']}'"


@pytest.mark.asyncio
async def test_execute_via_graph_uses_default_tenant_id_in_initial_state_when_whitespace():
    """When request.tenant_id is whitespace, initial_state['tenant_id'] must be the default."""
    captured = []
    use_case = _make_use_case(captured, default_tenant_id="global-default")

    request = ExecuteAgentInput(
        message="test",
        conv_id="conv-ws",
        user_id="user-ws",
        tenant_id="   ",
    )
    await use_case.execute(request)

    state = captured[0]
    assert state["tenant_id"] == "global-default", (
        f"initial_state['tenant_id'] must fall back to default_tenant_id when whitespace, "
        f"got '{state['tenant_id']}'"
    )


@pytest.mark.asyncio
async def test_execute_via_graph_normalizes_request_context_to_plain_dict():
    captured = []
    use_case = _make_use_case(captured)

    from agent_sdk.layer1_domain.entities.agent_request import RequestContext

    request = ExecuteAgentInput(
        message="hello",
        conv_id="conv-context",
        user_id="user-context",
        tenant_id="tenant-context",
        context=RequestContext(
            agent="bot",
            source="websocket",
            history=["first", "second"],
        ),
    )
    await use_case.execute(request)

    state = captured[0]
    assert state["context"] == {
        "agent": "bot",
        "source": "websocket",
        "history": ["first", "second"],
    }
