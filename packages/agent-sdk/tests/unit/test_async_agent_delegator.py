from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer1_domain.exceptions import RegistrationError


@pytest.mark.asyncio
async def test_async_agent_delegator_publishes_registry_queue_request_and_returns_interrupt():
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = [
        {
            "agent_type": "planner",
            "queue_metadata": {
                "queue_name": "planner.queue",
                "request_topic": "planner.request",
                "reply_topic": "planner.reply",
            },
        }
    ]

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
        compatibility_request_topic="agent.request",
    )

    request = AgentCallRequest(
        agent_id="planner-1",
        agent_type="planner",
        input_payload={"message": "plan this"},
        interrupt_id="int-1",
        thread_id="thread-1",
        correlation_id="corr-parent",
        reply_queue="parent.reply",
        context_snapshot={"shared_state": {"k": "v"}},
    )

    interrupt_payload = await delegator.delegate(request)

    publisher.publish.assert_awaited_once()
    kwargs = publisher.publish.await_args.kwargs
    assert kwargs["topic"] == "planner.request"
    assert kwargs["key"] == "corr-parent"
    assert kwargs["message"]["reply_queue"] == "parent.reply"
    assert kwargs["message"]["reply_topic"] == "planner.reply"
    assert kwargs["message"]["context_snapshot"] == {"shared_state": {"k": "v"}}
    assert interrupt_payload["correlation_id"] == "corr-parent"
    assert interrupt_payload["agent_id"] == "planner-1"


@pytest.mark.asyncio
async def test_async_agent_delegator_falls_back_to_compatibility_topic_when_registry_has_no_queue_metadata():
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = []

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
        compatibility_request_topic="agent.request.compat",
    )

    request = AgentCallRequest(
        agent_id="worker-1",
        agent_type="worker",
        input_payload={"message": "do work"},
        interrupt_id="int-2",
        thread_id="thread-2",
        correlation_id="corr-2",
    )

    await delegator.delegate(request)

    assert publisher.publish.await_args.kwargs["topic"] == "agent.request.compat"


@pytest.mark.asyncio
async def test_async_agent_delegator_falls_back_to_compatibility_topic_when_capability_lookup_fails():
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.side_effect = RegistrationError("404")

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
        compatibility_request_topic="agent.request.compat",
    )

    request = AgentCallRequest(
        agent_id="worker-1",
        agent_type="worker",
        input_payload={"message": "do work"},
        interrupt_id="int-3",
        thread_id="thread-3",
        correlation_id="corr-3",
    )

    await delegator.delegate(request)

    assert publisher.publish.await_args.kwargs["topic"] == "agent.request.compat"


@pytest.mark.asyncio
async def test_async_agent_delegator_publishes_parent_thread_routing_metadata():
    AsyncMock()
    AsyncMock()


@pytest.mark.asyncio
async def test_async_agent_delegator_preserves_flat_input_fields_for_execute_parsing():
    from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )
    from agent_sdk.layer2_application.services.message_reaction.default_handlers import (
        build_execute_input,
    )

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = []

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
    )

    await delegator.delegate(
        AgentCallRequest(
            agent_id="planner-1",
            agent_type="planner",
            input_payload={
                "message": "plan this",
                "file_id": "f-123",
                "action": "summarise",
            },
            interrupt_id="int-1",
            thread_id="thread-parent",
            correlation_id="corr-flat",
        )
    )

    request = build_execute_input(publisher.publish.await_args.kwargs["message"])

    assert request.message == "plan this"
    assert request.parameters == {"file_id": "f-123", "action": "summarise"}
    registry.list_capabilities.return_value = []

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
    )

    await delegator.delegate(
        AgentCallRequest(
            agent_id="planner-1",
            agent_type="planner",
            input_payload={"message": "plan this", "parameters": {"depth": 1}},
            interrupt_id="int-1",
            thread_id="thread-parent",
            correlation_id="corr-child",
        )
    )

    message = publisher.publish.await_args.kwargs["message"]
    assert message["input"] == {"message": "plan this", "parameters": {"depth": 1}}
    assert message["delegation"] == {"parent_thread_id": "thread-parent"}


@pytest.mark.asyncio
async def test_async_agent_delegator_caller_reply_topic_takes_precedence_over_queue_metadata():
    from agent_sdk.layer2_application.services.async_agent_delegator import (
        AsyncAgentDelegator,
    )

    publisher = AsyncMock()
    registry = AsyncMock()
    registry.list_capabilities.return_value = [
        {
            "agent_type": "planner",
            "queue_metadata": {
                "queue_name": "planner.queue",
                "request_topic": "planner.request",
                "reply_topic": "child.reply",
            },
        }
    ]

    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=registry,
    )

    request = AgentCallRequest(
        agent_id="planner-1",
        agent_type="planner",
        input_payload={"message": "plan this"},
        interrupt_id="int-1",
        thread_id="thread-1",
        correlation_id="corr-1",
        reply_topic="parent.reply",
    )

    await delegator.delegate(request)

    message = publisher.publish.await_args.kwargs["message"]
    assert message["reply_topic"] == "parent.reply"
