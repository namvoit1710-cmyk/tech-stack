from __future__ import annotations

import pytest

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer2_application.services.async_agent_delegator import (
    AsyncAgentDelegator,
)


class _Publisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, topic: str, message: dict, key=None) -> None:
        self.published.append({"topic": topic, "message": message, "key": key})


class _Registry:
    def __init__(self, queue_metadata) -> None:
        self._queue_metadata = queue_metadata

    async def list_capabilities(self) -> list[dict]:
        return [
            {
                "agent_type": "target-agent",
                "queue_metadata": self._queue_metadata,
            }
        ]


def _request(**overrides) -> AgentCallRequest:
    values = {
        "agent_id": "agent-001",
        "agent_type": "target-agent",
        "input_payload": {"message": "hello"},
        "interrupt_id": "interrupt-001",
        "thread_id": "thread-001",
        "correlation_id": "corr-001",
    }
    values.update(overrides)
    return AgentCallRequest(**values)


@pytest.mark.asyncio
async def test_delegate_keeps_agent_request_type_by_default() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry({"request_topic": "target.requests"}),
    )

    result = await delegator.delegate(_request())

    assert publisher.published == [
        {
            "topic": "target.requests",
            "key": "corr-001",
            "message": {
                "type": "agent.request.agent",
                "agent_id": "agent-001",
                "agent_type": "target-agent",
                "correlation_id": "corr-001",
                "thread_id": "thread-001",
                "session_id": None,
                "interrupt_id": "interrupt-001",
                "reply_topic": None,
                "reply_queue": None,
                "response_message_type": "agent.response",
                "metadata": {},
                "context_snapshot": {},
                "input": {"message": "hello"},
                "conv_id": "thread-001",
                "delegation": {"parent_thread_id": "thread-001"},
            },
        }
    ]
    assert result["request_topic"] == "target.requests"
    assert result["request_message_type"] == "agent.request.agent"
    assert result["response_message_type"] == "agent.response"


@pytest.mark.asyncio
async def test_delegate_uses_queue_metadata_request_message_type() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "reply_topic": "executor.status",
                "request_message_type": "agent.request.executor",
            }
        ),
    )

    result = await delegator.delegate(_request())

    message = publisher.published[0]["message"]
    assert publisher.published[0]["topic"] == "executor.requests"
    assert message["type"] == "agent.request.executor"
    assert message["reply_topic"] == "executor.status"
    assert message["response_message_type"] == "agent.response"
    assert result["request_message_type"] == "agent.request.executor"
    assert result["response_message_type"] == "agent.response"


@pytest.mark.asyncio
async def test_delegate_uses_queue_metadata_response_message_type() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "reply_topic": "executor.status",
                "request_message_type": "executor.request.agent",
                "response_message_type": "agent.step.status",
            }
        ),
    )

    result = await delegator.delegate(_request())

    message = publisher.published[0]["message"]
    assert publisher.published[0]["topic"] == "executor.requests"
    assert message["type"] == "executor.request.agent"
    assert message["reply_topic"] == "executor.status"
    assert message["response_message_type"] == "agent.step.status"
    assert result["request_message_type"] == "executor.request.agent"
    assert result["response_message_type"] == "agent.step.status"


@pytest.mark.asyncio
async def test_delegate_request_message_type_overrides_queue_metadata() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "request_message_type": "agent.request.executor",
            }
        ),
    )

    result = await delegator.delegate(
        _request(request_message_type="custom.request.type")
    )

    assert publisher.published[0]["message"]["type"] == "custom.request.type"
    assert result["request_message_type"] == "custom.request.type"


@pytest.mark.asyncio
async def test_delegate_response_message_type_overrides_queue_metadata() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "response_message_type": "agent.step.status",
            }
        ),
    )

    result = await delegator.delegate(
        _request(response_message_type="custom.response.type")
    )

    assert (
        publisher.published[0]["message"]["response_message_type"]
        == "custom.response.type"
    )
    assert result["response_message_type"] == "custom.response.type"


@pytest.mark.asyncio
async def test_delegate_uses_delivery_hints_response_message_type() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "delivery_hints": {
                    "response_message_type": "hinted.response.type",
                },
            }
        ),
    )

    result = await delegator.delegate(_request())

    assert (
        publisher.published[0]["message"]["response_message_type"]
        == "hinted.response.type"
    )
    assert result["response_message_type"] == "hinted.response.type"


@pytest.mark.asyncio
async def test_delegate_message_overrides_support_non_agent_envelopes() -> None:
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "request_message_type": "agent.request.executor",
                "response_message_type": "agent.step.status",
            }
        ),
    )

    await delegator.delegate(
        _request(
            message_overrides={
                "message_id": "msg-001",
                "conv_id": "conv-001",
                "payload": {
                    "from": "business.agent",
                    "to": "executor.runtime",
                    "type": "step_batch",
                    "batch_id": "batch-001",
                    "steps": [],
                },
            }
        )
    )

    message = publisher.published[0]["message"]
    assert message["type"] == "agent.request.executor"
    assert message["response_message_type"] == "agent.step.status"
    assert message["message_id"] == "msg-001"
    assert message["conv_id"] == "conv-001"
    assert message["payload"]["type"] == "step_batch"


@pytest.mark.asyncio
async def test_delegate_message_overrides_cannot_replace_resolved_message_types() -> (
    None
):
    publisher = _Publisher()
    delegator = AsyncAgentDelegator(
        publisher=publisher,
        registry=_Registry(
            {
                "request_topic": "executor.requests",
                "request_message_type": "agent.request.executor",
                "response_message_type": "agent.step.status",
            }
        ),
    )

    await delegator.delegate(
        _request(
            message_overrides={
                "type": "bad.request.type",
                "response_message_type": "bad.response.type",
            }
        )
    )

    message = publisher.published[0]["message"]
    assert message["type"] == "agent.request.executor"
    assert message["response_message_type"] == "agent.step.status"
