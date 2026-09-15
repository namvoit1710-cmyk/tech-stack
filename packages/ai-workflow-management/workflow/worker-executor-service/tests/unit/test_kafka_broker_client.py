"""Unit tests for KafkaBrokerClient and Kafka messaging adapters.

All tests mock ``confluent_kafka.Producer``/``Consumer`` so no real Kafka
broker is needed.
"""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_message(topic: str, value: dict, error=None, partition=0, offset=0):
    """Return a mock Kafka message object."""
    msg = MagicMock()
    msg.topic.return_value = topic
    msg.value.return_value = json.dumps(value).encode("utf-8")
    msg.error.return_value = error
    msg.partition.return_value = partition
    msg.offset.return_value = offset
    return msg


# ---------------------------------------------------------------------------
# Producer / publish_to_topic tests
# ---------------------------------------------------------------------------

class TestPublishToTopic:

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_publish_serializes_json_and_produces(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        message = {"type": "test", "data": 42}
        client.publish_to_topic("my-topic", message, key="k1")

        producer.produce.assert_called_once()
        args, kwargs = producer.produce.call_args
        assert args[0] == "my-topic"
        assert json.loads(kwargs["value"].decode("utf-8")) == message
        assert kwargs["key"] == b"k1"
        producer.poll.assert_called_with(0)

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_publish_raises_on_oversize_message(self, MockProducer):
        """SA-1951 — a >1 MiB message is REJECTED, typed, before the produce.

        This replaces the SA-1941 WARN-only assertion. Warning and then producing
        anyway is what lost the result: the broker rejects the message, the
        caller sees an untyped error it does not catch, and the task hangs. The
        caller now catches ``MessageTooLargeError`` and degrades to a small
        ``task.failed`` — so this must raise, and must NOT produce.
        """
        from app.layer1_domain.errors import MessageTooLargeError
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        big = {"blob": "x" * (2 * 1024 * 1024)}  # ~2 MiB serialized

        with pytest.raises(MessageTooLargeError) as exc:
            client.publish_to_topic("results", big)

        assert exc.value.max_bytes == 1024 * 1024
        assert exc.value.size_bytes > exc.value.max_bytes
        producer.produce.assert_not_called()  # never hand the broker a doomed message

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_publish_with_no_key(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.publish_to_topic("t", {"a": 1})

        _, kwargs = producer.produce.call_args
        assert kwargs["key"] is None

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_publish_handles_buffer_error_with_flush_retry(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer
        producer.produce.side_effect = [BufferError("full"), None]

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.publish_to_topic("t", {"x": 1})

        assert producer.produce.call_count == 2
        producer.flush.assert_called_once_with(timeout=5)

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_delivery_callback_logs_error(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        msg = MagicMock()
        msg.topic.return_value = "t"

        with patch("app.layer4_frameworks.messaging.kafka_broker_client.logger") as mock_logger:
            KafkaBrokerClient._delivery_callback("some error", msg)
            mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# subscribe_to_topic tests
# ---------------------------------------------------------------------------

class TestSubscribeToTopic:

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_creates_consumer_on_first_subscribe(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        consumer.poll.return_value = None
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        assert client._sub_consumer is None

        client.subscribe_to_topic("topic-a", MagicMock())

        MockConsumer.assert_called_once()
        consumer.subscribe.assert_called_with(["topic-a"])
        assert client._consumer_thread is not None
        assert client._consumer_thread.daemon is True

        client.close()

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_async_callback_dispatched(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        received = []

        async def handler(msg):
            received.append(msg)

        consumer = MagicMock()
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                return _make_mock_message("t1", {"hello": "world"})
            return None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")

        import asyncio, time, threading
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client.set_event_loop(loop)
        client.subscribe_to_topic("t1", handler)

        time.sleep(0.5)

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()
        assert len(received) == 1
        assert received[0] == {"hello": "world"}


# ---------------------------------------------------------------------------
# Queue-based methods
# ---------------------------------------------------------------------------

class TestQueueMethods:

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_consume_from_queue_returns_message(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        consumer.poll.return_value = _make_mock_message(
            "workflow.tasks", {"task_id": "t1"}, partition=2, offset=42
        )
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        result = client.consume_from_queue("workflow.tasks")

        assert result is not None
        assert result["data"] == {"task_id": "t1"}
        assert result["message_id"] == "2:42"

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_consume_from_queue_returns_none_on_timeout(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        consumer.poll.return_value = None
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        result = client.consume_from_queue("workflow.tasks")

        assert result is None

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_consume_from_queue_returns_none_on_bad_json(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        bad_msg = MagicMock()
        bad_msg.topic.return_value = "workflow.tasks"
        bad_msg.value.return_value = b"not json{{"
        bad_msg.error.return_value = None
        consumer.poll.return_value = bad_msg
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        result = client.consume_from_queue("workflow.tasks")

        assert result is None

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_publish_to_queue_delegates_to_publish_to_topic(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.publish_to_queue("my-queue", {"task": "t1"})

        producer.produce.assert_called_once()
        args, _ = producer.produce.call_args
        assert args[0] == "my-queue"

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_publish_to_dlq(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.publish_to_dlq("workflow.tasks", {"task": "t1"}, "parse error")

        producer.produce.assert_called_once()
        args, kwargs = producer.produce.call_args
        assert args[0] == "workflow.tasks.dlq"
        envelope = json.loads(kwargs["value"].decode("utf-8"))
        assert envelope["original_topic"] == "workflow.tasks"
        assert envelope["error"] == "parse error"
        assert envelope["message"] == {"task": "t1"}

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_queue_management_noop(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")

        assert client.queue_exists("q1") is True
        assert client.subscription_exists("q1", "t1") is True

        # These should not raise
        client.create_queue("q1")
        client.delete_queue("q1")
        client.create_subscription("q1", "t1")
        client.delete_subscription("q1", "t1")
        client.acknowledge("q1", "m1")


# ---------------------------------------------------------------------------
# close tests
# ---------------------------------------------------------------------------

class TestClose:

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_close_stops_thread_and_cleans_up(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        consumer.poll.return_value = None
        MockConsumer.return_value = consumer

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.subscribe_to_topic("t1", MagicMock())

        assert client._running is True

        client.close()

        assert client._running is False
        assert client._consumer_thread is None
        assert client._sub_consumer is None
        consumer.close.assert_called_once()
        producer.flush.assert_called_with(timeout=10)

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_close_cleans_up_queue_consumer(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        queue_consumer = MagicMock()
        queue_consumer.poll.return_value = None
        MockConsumer.return_value = queue_consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.consume_from_queue("workflow.tasks")

        client.close()

        assert client._queue_consumer is None
        queue_consumer.close.assert_called_once()


# ---------------------------------------------------------------------------
# KafkaResultPublisher tests
# ---------------------------------------------------------------------------

class TestKafkaResultPublisher:

    def test_publish_dict_result(self):
        from app.layer4_frameworks.messaging.kafka_result_publisher import KafkaResultPublisher

        broker = MagicMock()
        pub = KafkaResultPublisher(broker, topic="workflow.results")
        pub.publish_result({"task_id": "t1", "status": "COMPLETED"})

        broker.publish_to_topic.assert_called_once()
        topic, payload = broker.publish_to_topic.call_args[0]
        assert topic == "workflow.results"
        assert payload["task_id"] == "t1"

    def test_publish_dataclass_result(self):
        from app.layer4_frameworks.messaging.kafka_result_publisher import KafkaResultPublisher

        @dataclass
        class TaskResult:
            task_id: str = ""
            status: str = ""

        broker = MagicMock()
        pub = KafkaResultPublisher(broker)
        pub.publish_result(TaskResult(task_id="t2", status="DONE"))

        payload = broker.publish_to_topic.call_args[0][1]
        assert payload["task_id"] == "t2"
        assert payload["status"] == "DONE"

    def test_publish_string_result(self):
        from app.layer4_frameworks.messaging.kafka_result_publisher import KafkaResultPublisher

        broker = MagicMock()
        pub = KafkaResultPublisher(broker)
        pub.publish_result("raw string")

        payload = broker.publish_to_topic.call_args[0][1]
        assert payload == {"data": "raw string"}


# ---------------------------------------------------------------------------
# KafkaTaskQueue tests
# ---------------------------------------------------------------------------

class TestKafkaTaskQueue:

    def test_consume_subscribes_to_topic(self):
        from app.layer4_frameworks.messaging.kafka_task_queue import KafkaTaskQueue

        broker = MagicMock()
        queue = KafkaTaskQueue(broker, topic="workflow.tasks")

        callback = MagicMock()
        queue.consume(callback)

        broker.subscribe_to_topic.assert_called_once_with("workflow.tasks", callback)

    def test_acknowledge_is_noop(self):
        from app.layer4_frameworks.messaging.kafka_task_queue import KafkaTaskQueue

        broker = MagicMock()
        queue = KafkaTaskQueue(broker)
        # Should not raise
        queue.acknowledge({"message_id": "m1"})


# ---------------------------------------------------------------------------
# Bootstrap integration tests
# ---------------------------------------------------------------------------

class TestBootstrapKafkaIntegration:

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer", MagicMock())
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_bootstrap_local_mode_creates_broker(self):
        from app.layer4_frameworks.config.app_config import settings

        original_messaging_mode = settings.MESSAGING_MODE
        original_infra_mode = settings.INFRA_MODE

        try:
            settings.MESSAGING_MODE = "local"

            from bootstrap import build_app_container
            container = build_app_container()

            assert "_broker" in container
            assert hasattr(container["_broker"], "publish_to_topic")
            assert hasattr(container["_broker"], "consume_from_queue")
            assert hasattr(container["_broker"], "close")
        finally:
            settings.MESSAGING_MODE = original_messaging_mode
            settings.INFRA_MODE = original_infra_mode

    def test_bootstrap_mock_mode_no_broker(self):
        from app.layer4_frameworks.config.app_config import settings

        original_messaging_mode = settings.MESSAGING_MODE
        original_infra_mode = settings.INFRA_MODE

        try:
            settings.MESSAGING_MODE = ""
            settings.INFRA_MODE = "mock"

            from bootstrap import build_app_container
            container = build_app_container()

            assert "_broker" not in container
        finally:
            settings.MESSAGING_MODE = original_messaging_mode
            settings.INFRA_MODE = original_infra_mode
