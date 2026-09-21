from __future__ import annotations

import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
    KafkaBrokerClient,
)
from agent_sdk.layer4_frameworks.messaging.kafka_consumer import KafkaMessageConsumer
from agent_sdk.layer4_frameworks.messaging.kafka_publisher import KafkaMessagePublisher


class _StubLogger:
    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _RecordingBroker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def ensure_topic_exists(self, topic: str) -> None:
        self.calls.append(("ensure", topic))

    def publish_to_topic(self, topic: str, message: dict, key=None) -> None:
        self.calls.append(("publish", topic))

    def subscribe_to_topic(self, topic: str, callback) -> None:
        self.calls.append(("subscribe", topic))


@pytest.mark.asyncio
async def test_kafka_message_consumer_provisions_topic_before_subscribe():
    broker = _RecordingBroker()
    consumer = KafkaMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=_StubLogger(),
    )

    await consumer.start(AsyncMock())

    assert broker.calls == [
        ("ensure", "agent.request"),
        ("subscribe", "agent.request"),
    ]


@pytest.mark.asyncio
async def test_kafka_message_publisher_provisions_outbound_topic_once():
    broker = _RecordingBroker()
    publisher = KafkaMessagePublisher(broker_client=broker, logger=_StubLogger())

    await publisher.publish("agent.reply.dynamic", {"status": "ok"})
    await publisher.publish("agent.reply.dynamic", {"status": "ok-again"})

    assert broker.calls == [
        ("ensure", "agent.reply.dynamic"),
        ("publish", "agent.reply.dynamic"),
        ("publish", "agent.reply.dynamic"),
    ]


def test_kafka_broker_client_ensure_topic_exists_is_idempotent(monkeypatch):
    created_topics: list[tuple[str, int, int]] = []
    state = {"topics": {}}

    class _FakeNewTopic:
        def __init__(
            self,
            topic: str,
            num_partitions: int,
            replication_factor: int,
        ) -> None:
            self.topic = topic
            self.num_partitions = num_partitions
            self.replication_factor = replication_factor

    class _ResolvedFuture:
        def result(self) -> None:
            return None

    class _FakeAdminClient:
        def __init__(self, config: dict[str, str]) -> None:
            self.config = config

        def list_topics(self, timeout: int) -> SimpleNamespace:
            return SimpleNamespace(topics=dict(state["topics"]))

        def create_topics(
            self, topics: list[_FakeNewTopic]
        ) -> dict[str, _ResolvedFuture]:
            for topic in topics:
                created_topics.append(
                    (topic.topic, topic.num_partitions, topic.replication_factor)
                )
                state["topics"][topic.topic] = object()
            return {topic.topic: _ResolvedFuture() for topic in topics}

    fake_package = ModuleType("confluent_kafka")
    fake_admin_module = ModuleType("confluent_kafka.admin")
    fake_admin_module.AdminClient = _FakeAdminClient
    fake_admin_module.NewTopic = _FakeNewTopic
    fake_package.admin = fake_admin_module

    monkeypatch.setitem(sys.modules, "confluent_kafka", fake_package)
    monkeypatch.setitem(sys.modules, "confluent_kafka.admin", fake_admin_module)

    broker = KafkaBrokerClient.__new__(KafkaBrokerClient)
    broker._bootstrap_servers = "localhost:9092"
    broker._admin_client = None
    broker._admin_lock = threading.Lock()

    broker.ensure_topic_exists("agent.request")
    broker.ensure_topic_exists("agent.request")

    assert created_topics == [("agent.request", 1, 1)]
