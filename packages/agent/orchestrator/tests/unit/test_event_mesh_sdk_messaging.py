from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_sdk.layer4_frameworks.messaging import (
    EventMeshMessageConsumer,
)
from agent_sdk.layer4_frameworks.messaging import (
    EventMeshMessagePublisher,
)


class _QueueBuilderStub:
    def __init__(self):
        self.messages = []

    def build_all_queues(self):
        return {"orchestrator.test.response": "_on_message"}

    async def _on_message(self, message):
        self.messages.append(message)


class _BrokerStub:
    def __init__(self):
        self.callbacks = {}
        self.acked = []
        self.nacked = []
        self.closed = 0
        self.published = []

    def build_queue_name(self, topic):
        return f"default/{topic}"

    def subscribe_to_topic(self, topic, callback):
        self.callbacks[topic] = callback

    def ack_message(self, queue_name, message_headers):
        self.acked.append((queue_name, message_headers))

    def nack_message(self, queue_name, message_headers, requeue=True):
        self.nacked.append((queue_name, message_headers, requeue))

    def close(self):
        self.closed += 1

    def publish_to_topic(self, topic, message, key=None):
        self.published.append((topic, message, key))


@pytest.mark.asyncio
async def test_event_mesh_consumer_adapter_nacks_after_handler_failure():
    broker = _BrokerStub()

    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="orchestrator.test.response",
        logger=MagicMock(),
    )

    async def _handle(delivery):
        raise RuntimeError(f"boom: {delivery.payload['correlation_id']}")

    await consumer.start(_handle)
    await broker.callbacks["orchestrator.test.response"](
        {"correlation_id": "corr-2"},
    )

    assert broker.acked == []
    assert broker.nacked == []


@pytest.mark.asyncio
async def test_event_mesh_publisher_adapter_publishes_expected_topics():
    broker = _BrokerStub()
    publisher = EventMeshMessagePublisher(
        broker_client=broker,
        logger=MagicMock(),
    )

    await publisher.publish(
        "agent.request",
        {"correlation_id": "corr-a", "kind": "agent"},
        key="corr-a",
    )
    await publisher.publish(
        "orchestrator.request",
        {"correlation_id": "corr-o", "kind": "orchestrator"},
        key="corr-o",
    )

    assert broker.published == [
        ("agent.request", {"correlation_id": "corr-a", "kind": "agent"}, "corr-a"),
        (
            "orchestrator.request",
            {"correlation_id": "corr-o", "kind": "orchestrator"},
            "corr-o",
        ),
    ]