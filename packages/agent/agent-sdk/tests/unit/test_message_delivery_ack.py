from __future__ import annotations

import asyncio
import json
import queue
import sys
import time
from types import ModuleType
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_kafka_delivery_ack_commits_message_once():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    committed: list[tuple[object, bool]] = []

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def value(self):
            return json.dumps({"correlation_id": "corr-1"}).encode("utf-8")

    class _Broker:
        def subscribe_to_topic(self, topic, callback):
            self.topic = topic
            self.callback = callback

        def commit_message(self, message, asynchronous=False):
            committed.append((message, asynchronous))

    broker = _Broker()
    consumer = KafkaMessageConsumer(
        broker_client=broker, topic="agent.request", logger=MagicMock()
    )

    await consumer.start(lambda delivery: delivery.ack())
    await broker.callback(_KafkaMessage())

    assert len(committed) == 1
    assert committed[0][1] is False


@pytest.mark.asyncio
async def test_kafka_delivery_nack_requests_retry_without_commit():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    nacked: list[tuple[object, bool]] = []

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def value(self):
            return json.dumps({"correlation_id": "corr-nack"}).encode("utf-8")

    class _Broker:
        def subscribe_to_topic(self, topic, callback):
            self.callback = callback

        def nack_message(self, message, requeue=True):
            nacked.append((message, requeue))

    broker = _Broker()
    consumer = KafkaMessageConsumer(
        broker_client=broker, topic="agent.request", logger=MagicMock()
    )

    await consumer.start(lambda delivery: delivery.nack(requeue=True))
    message = _KafkaMessage()
    await broker.callback(message)

    assert nacked == [(message, True)]


@pytest.mark.asyncio
async def test_kafka_delivery_reject_calls_broker_reject_path():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    rejected: list[tuple[object, str | None]] = []

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def value(self):
            return json.dumps({"correlation_id": "corr-reject"}).encode("utf-8")

    class _Broker:
        def subscribe_to_topic(self, topic, callback):
            self.callback = callback

        def reject_message(self, message, reason=None):
            rejected.append((message, reason))

    broker = _Broker()
    consumer = KafkaMessageConsumer(
        broker_client=broker, topic="agent.request", logger=MagicMock()
    )

    await consumer.start(lambda delivery: delivery.reject())
    message = _KafkaMessage()
    await broker.callback(message)

    assert rejected == [(message, None)]


@pytest.mark.asyncio
async def test_kafka_decode_failure_uses_reject_path():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    rejected: list[tuple[object, str | None]] = []
    logger = MagicMock()

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def value(self):
            return b"not-json"

    class _Broker:
        def subscribe_to_topic(self, topic, callback):
            self.callback = callback

        def reject_message(self, message, reason=None):
            rejected.append((message, reason))

    broker = _Broker()
    consumer = KafkaMessageConsumer(
        broker_client=broker, topic="agent.request", logger=logger
    )

    await consumer.start(lambda delivery: delivery.ack())
    message = _KafkaMessage()
    await broker.callback(message)

    assert rejected == [(message, "decode_error")]
    logger.error.assert_called_once()


@pytest.mark.asyncio
async def test_event_mesh_delivery_ack_calls_broker_ack():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    acked: list[tuple[str, dict[str, str]]] = []

    class _Broker:
        def build_queue_name(self, topic):
            return f"event-mesh-ns/{topic}"

        def subscribe_to_topic(self, topic, callback):
            self.callback = callback

        def ack_message(self, queue_name, message_headers):
            acked.append((queue_name, message_headers))

    broker = _Broker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=MagicMock(),
    )

    await consumer.start(lambda delivery: delivery.ack())
    await broker.callback({"correlation_id": "corr-2"}, {"x-message-id": "mid-1"})

    assert acked == [("event-mesh-ns/agent.request", {"x-message-id": "mid-1"})]


@pytest.mark.asyncio
async def test_event_mesh_delivery_nack_requeues_by_skipping_ack():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    broker = MagicMock()
    broker.build_queue_name.return_value = "event-mesh-ns/agent.request"
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=MagicMock(),
    )

    await consumer.start(lambda delivery: delivery.nack(requeue=True))
    await broker.subscribe_to_topic.call_args[0][1](
        {"correlation_id": "corr-3"}, {"x-message-id": "mid-2"}
    )

    broker.nack_message.assert_called_once_with(
        "event-mesh-ns/agent.request",
        {"x-message-id": "mid-2"},
        requeue=True,
    )
    broker.ack_message.assert_not_called()


@pytest.mark.asyncio
async def test_event_mesh_delivery_reject_dead_letters_without_requeue():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    rejected: list[tuple[str, dict[str, str], bool]] = []

    class _Broker:
        def build_queue_name(self, topic):
            return f"event-mesh-ns/{topic}"

        def subscribe_to_topic(self, topic, callback):
            self.callback = callback

        def reject_message(self, queue_name, message_headers, requeue=False):
            rejected.append((queue_name, message_headers, requeue))

    broker = _Broker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=MagicMock(),
    )

    await consumer.start(lambda delivery: delivery.reject())
    await broker.callback({"correlation_id": "corr-4"}, {"x-message-id": "mid-3"})

    assert rejected == [
        ("event-mesh-ns/agent.request", {"x-message-id": "mid-3"}, False)
    ]


@pytest.mark.asyncio
async def test_kafka_consumer_stop_closes_broker_client_when_available():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    broker = MagicMock()
    consumer = KafkaMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=MagicMock(),
    )

    await consumer.stop()

    broker.close.assert_called_once()


@pytest.mark.asyncio
async def test_event_mesh_consumer_stop_closes_broker_client_when_available():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    broker = MagicMock()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=MagicMock(),
    )

    await consumer.stop()

    broker.close.assert_called_once()


@pytest.mark.asyncio
async def test_kafka_message_publisher_publish_keeps_loop_responsive():
    from agent_sdk.layer4_frameworks.messaging.kafka_publisher import (
        KafkaMessagePublisher,
    )

    class _Broker:
        def publish_to_topic(self, topic, message, key=None):
            time.sleep(0.05)

    publisher = KafkaMessagePublisher(_Broker(), MagicMock())
    task = asyncio.create_task(publisher.publish("agent.request", {"message": "hi"}))

    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.02)
    await task


@pytest.mark.asyncio
async def test_event_mesh_message_publisher_publish_keeps_loop_responsive():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_publisher import (
        EventMeshMessagePublisher,
    )

    class _Broker:
        def publish_to_topic(self, topic, message, key=None):
            time.sleep(0.05)

    publisher = EventMeshMessagePublisher(_Broker(), MagicMock())
    task = asyncio.create_task(publisher.publish("agent.request", {"message": "hi"}))

    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.02)
    await task


@pytest.mark.asyncio
async def test_kafka_message_consumer_start_keeps_loop_responsive():
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )

    class _Broker:
        def subscribe_to_topic(self, topic, callback):
            time.sleep(0.05)

    consumer = KafkaMessageConsumer(
        broker_client=_Broker(), topic="agent.request", logger=MagicMock()
    )

    async def _handler(delivery):
        return None

    task = asyncio.create_task(consumer.start(_handler))
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.02)
    await task


@pytest.mark.asyncio
async def test_event_mesh_message_consumer_start_keeps_loop_responsive():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    class _Broker:
        def subscribe_to_topic(self, topic, callback):
            time.sleep(0.05)

    consumer = EventMeshMessageConsumer(
        broker_client=_Broker(), topic="agent.request", logger=MagicMock()
    )

    async def _handler(delivery):
        return None

    task = asyncio.create_task(consumer.start(_handler))
    await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.02)
    await task


def test_kafka_broker_disables_auto_commit_for_sdk_controlled_ack():
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    broker = object.__new__(KafkaBrokerClient)
    broker._bootstrap_servers = "localhost:9092"
    broker._group_id = "group"
    broker._auto_offset_reset = "earliest"
    broker._producer = MagicMock()
    broker._consumer = None
    broker._callbacks = {}
    broker._lock = MagicMock()
    broker._consumer_thread = None
    broker._running = False
    broker._consumer_conf = {
        "bootstrap.servers": "localhost:9092",
        "group.id": "group",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
    }

    assert broker._consumer_conf["enable.auto.commit"] is False


def test_kafka_broker_client_filters_sdk_only_config_before_confluent_init(
    monkeypatch,
):
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    captured: dict[str, dict[str, object]] = {}

    class _Producer:
        def __init__(self, conf):
            captured["producer"] = dict(conf)

        def poll(self, timeout):
            return None

        def produce(self, *args, **kwargs):
            return None

        def flush(self, timeout=None):
            return 0

    confluent_kafka = ModuleType("confluent_kafka")
    confluent_kafka.Producer = _Producer
    monkeypatch.setitem(sys.modules, "confluent_kafka", confluent_kafka)

    broker = KafkaBrokerClient(
        bootstrap_servers="localhost:9092",
        group_id="agent-sdk-consumer",
        kafka_reject_topic="agent.request.dead-letter",
    )

    assert broker._kafka_reject_topic == "agent.request.dead-letter"
    assert "kafka_reject_topic" not in captured["producer"]
    assert "kafka_reject_topic" not in broker._consumer_conf


def test_kafka_delivery_resolution_uses_consumer_thread_control_queue():
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    class _KafkaMessage:
        def __init__(self, offset: int) -> None:
            self._offset = offset

        def topic(self):
            return "agent.request"

        def partition(self):
            return 0

        def offset(self):
            return self._offset

        def key(self):
            return b"corr-1"

        def value(self):
            return b'{"bad":true}'

        def headers(self):
            return []

    broker = object.__new__(KafkaBrokerClient)
    broker._producer = MagicMock()
    broker._consumer = MagicMock()
    broker._kafka_reject_topic = "agent.request.dead-letter"
    broker._consumer_command_queue = queue.Queue()
    broker._next_commit_offset = {}
    broker._inflight_offsets = {}
    broker._resolved_messages = {}

    message = _KafkaMessage(19)
    broker.commit_message(message, asynchronous=False)
    broker.nack_message(message, requeue=True)
    broker.reject_message(message, reason="decode_error")

    broker._consumer.commit.assert_not_called()
    broker._consumer.seek.assert_not_called()
    broker._producer.produce.assert_not_called()

    commands: list[dict[str, object]] = []
    while not broker._consumer_command_queue.empty():
        commands.append(broker._consumer_command_queue.get_nowait())

    assert [command["action"] for command in commands] == [
        "ack",
        "seek",
        "reject",
    ]


def test_kafka_broker_commits_only_contiguous_offsets():
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    class _KafkaMessage:
        def __init__(self, offset: int) -> None:
            self._offset = offset

        def topic(self):
            return "agent.request"

        def partition(self):
            return 0

        def offset(self):
            return self._offset

    broker = object.__new__(KafkaBrokerClient)
    broker._producer = MagicMock()
    broker._consumer = MagicMock()
    broker._consumer_command_queue = queue.Queue()
    broker._next_commit_offset = {("agent.request", 0): 100}
    broker._inflight_offsets = {("agent.request", 0): {100, 101, 102}}
    broker._resolved_messages = {("agent.request", 0): {}}

    message_100 = _KafkaMessage(100)
    message_101 = _KafkaMessage(101)
    message_102 = _KafkaMessage(102)

    broker.commit_message(message_102, asynchronous=False)
    broker._drain_consumer_commands()
    broker._consumer.commit.assert_not_called()

    broker.commit_message(message_100, asynchronous=False)
    broker._drain_consumer_commands()
    first_commit = broker._consumer.commit.call_args_list[0].kwargs
    assert first_commit["message"] is message_100
    assert first_commit["asynchronous"] is False

    broker.commit_message(message_101, asynchronous=False)
    broker._drain_consumer_commands()
    second_commit = broker._consumer.commit.call_args_list[1].kwargs
    assert second_commit["message"] is message_102
    assert second_commit["asynchronous"] is False


def test_kafka_broker_reject_message_publishes_dead_letter_and_commits():
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def partition(self):
            return 0

        def offset(self):
            return 19

        def key(self):
            return b"corr-1"

        def value(self):
            return b'{"bad":true}'

        def headers(self):
            return [("x-correlation-id", b"corr-1")]

    broker = object.__new__(KafkaBrokerClient)
    broker._producer = MagicMock()
    broker._consumer = MagicMock()
    broker._kafka_reject_topic = "agent.request.dead-letter"
    broker._consumer_command_queue = queue.Queue()
    broker._next_commit_offset = {("agent.request", 0): 19}
    broker._inflight_offsets = {("agent.request", 0): {19}}
    broker._resolved_messages = {("agent.request", 0): {}}

    message = _KafkaMessage()
    broker.reject_message(message, reason="decode_error")

    broker._producer.produce.assert_not_called()
    broker._consumer.commit.assert_not_called()

    broker._drain_consumer_commands()

    broker._producer.produce.assert_called_once_with(
        "agent.request.dead-letter",
        value=message.value(),
        key=message.key(),
        headers=[
            ("x-correlation-id", "corr-1"),
            ("x-original-topic", "agent.request"),
            ("x-reject-reason", "decode_error"),
        ],
        callback=broker._delivery_callback,
    )
    broker._producer.poll.assert_called_once_with(0)
    broker._consumer.commit.assert_called_once_with(message=message, asynchronous=False)


def test_kafka_broker_reject_message_commits_when_dead_letter_topic_missing():
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def partition(self):
            return 0

        def offset(self):
            return 23

    broker = object.__new__(KafkaBrokerClient)
    broker._producer = MagicMock()
    broker._consumer = MagicMock()
    broker._kafka_reject_topic = None
    broker._consumer_command_queue = queue.Queue()
    broker._next_commit_offset = {("agent.request", 0): 23}
    broker._inflight_offsets = {("agent.request", 0): {23}}
    broker._resolved_messages = {("agent.request", 0): {}}

    message = _KafkaMessage()
    broker.reject_message(message, reason="poison")

    broker._drain_consumer_commands()

    broker._producer.produce.assert_not_called()
    broker._consumer.commit.assert_called_once_with(message=message, asynchronous=False)


def test_kafka_broker_nack_message_rewinds_current_offset_for_retry(monkeypatch):
    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    class _TopicPartition:
        def __init__(self, topic, partition, offset):
            self.topic = topic
            self.partition = partition
            self.offset = offset

    confluent_kafka = ModuleType("confluent_kafka")
    confluent_kafka.TopicPartition = _TopicPartition
    monkeypatch.setitem(sys.modules, "confluent_kafka", confluent_kafka)

    class _KafkaMessage:
        def topic(self):
            return "agent.request"

        def partition(self):
            return 2

        def offset(self):
            return 19

    broker = object.__new__(KafkaBrokerClient)
    broker._consumer = MagicMock()
    broker._consumer_command_queue = queue.Queue()
    broker._next_commit_offset = {}
    broker._inflight_offsets = {}
    broker._resolved_messages = {}

    broker.nack_message(_KafkaMessage(), requeue=True)
    broker._drain_consumer_commands()

    broker._consumer.seek.assert_called_once()
    topic_partition = broker._consumer.seek.call_args.args[0]
    assert topic_partition.topic == "agent.request"
    assert topic_partition.partition == 2
    assert topic_partition.offset == 19
    broker._consumer.commit.assert_not_called()


def test_event_mesh_consumer_dispatch_accepts_payload_and_headers():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    broker = MagicMock()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=MagicMock(),
    )

    async def _handler(delivery):
        return None

    asyncio.run(consumer.start(_handler))
    callback = broker.subscribe_to_topic.call_args[0][1]
    assert callback.__code__.co_argcount == 3


@pytest.mark.asyncio
async def test_event_mesh_consumer_subscribes_to_multiple_topics_with_shared_queue():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    subscribed: dict[str, object] = {}

    class _Broker:
        def build_queue_name(self, topic):
            return f"event-mesh-ns/{topic}"

        def subscribe_to_topics(self, topics, callback, queue_name=None):
            subscribed["topics"] = list(topics)
            subscribed["callback"] = callback
            subscribed["queue_name"] = queue_name

        def ack_message(self, queue_name, message_headers):
            subscribed.setdefault("acked", []).append((queue_name, message_headers))

    broker = _Broker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topics=["agent.request", "agent.response", "agent.request"],
        queue_name="event-mesh-ns/shared",
        logger=MagicMock(),
    )

    async def _handler(delivery):
        await delivery.ack()

    await consumer.start(_handler)

    assert subscribed["topics"] == ["agent.request", "agent.response"]
    assert subscribed["queue_name"] == "event-mesh-ns/shared"
    assert subscribed["callback"].__code__.co_argcount == 3

    await subscribed["callback"](
        {"correlation_id": "corr-5"},
        {"x-message-id": "mid-5"},
        "event-mesh-ns/shared",
    )

    assert subscribed["acked"] == [("event-mesh-ns/shared", {"x-message-id": "mid-5"})]
