"""Unit tests for KafkaBrokerClient and Kafka messaging integration.

All tests mock ``confluent_kafka.Producer``/``Consumer`` so no real Kafka
broker is needed.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_message(topic: str, value: dict, error=None):
    """Return a mock Kafka message object."""
    msg = MagicMock()
    msg.topic.return_value = topic
    msg.value.return_value = json.dumps(value).encode("utf-8")
    msg.error.return_value = error
    msg.partition.return_value = 0
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

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_delivery_callback_logs_debug_on_success(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        msg = MagicMock()
        msg.topic.return_value = "t"
        msg.partition.return_value = 0

        with patch("app.layer4_frameworks.messaging.kafka_broker_client.logger") as mock_logger:
            KafkaBrokerClient._delivery_callback(None, msg)
            mock_logger.debug.assert_called_once()


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
        assert client._consumer is None

        client.subscribe_to_topic("topic-a", MagicMock())

        MockConsumer.assert_called_once()
        consumer.subscribe.assert_called_with(["topic-a"])
        assert client._consumer_thread is not None
        assert client._consumer_thread.daemon is True

        client.close()

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_resubscribes_with_full_topic_list(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        consumer.poll.return_value = None
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.subscribe_to_topic("topic-a", MagicMock())
        client.subscribe_to_topic("topic-b", MagicMock())

        last_call = consumer.subscribe.call_args_list[-1]
        topics_arg = last_call[0][0]
        assert set(topics_arg) == {"topic-a", "topic-b"}

        client.close()

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_starts_daemon_thread_only_once(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        consumer.poll.return_value = None
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.subscribe_to_topic("t1", MagicMock())
        thread1 = client._consumer_thread
        client.subscribe_to_topic("t2", MagicMock())
        thread2 = client._consumer_thread

        assert thread1 is thread2

        client.close()


# ---------------------------------------------------------------------------
# Consumer dispatch tests
# ---------------------------------------------------------------------------

class TestConsumerDispatch:

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

        # Provide a running event loop for run_coroutine_threadsafe
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.set_event_loop(loop)
        client.subscribe_to_topic("t1", handler)

        import time
        time.sleep(0.5)

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()
        assert len(received) == 1
        assert received[0] == {"hello": "world"}

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_deserialization_error_skips_message(self, MockConsumer, MockProducer):
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
                bad_msg = MagicMock()
                bad_msg.topic.return_value = "t1"
                bad_msg.value.return_value = b"not json{{"
                bad_msg.error.return_value = None
                return bad_msg
            if poll_count == 2:
                return _make_mock_message("t1", {"ok": True})
            return None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        # Provide a running event loop for run_coroutine_threadsafe
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.set_event_loop(loop)
        client.subscribe_to_topic("t1", handler)

        import time
        time.sleep(0.5)

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()
        assert len(received) == 1
        assert received[0] == {"ok": True}

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_callback_error_does_not_crash_loop(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        received = []

        async def bad_handler(msg):
            raise RuntimeError("handler boom")

        async def good_handler(msg):
            received.append(msg)

        consumer = MagicMock()
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            if poll_count == 1:
                return _make_mock_message("t1", {"msg": 1})
            if poll_count == 2:
                return _make_mock_message("t1", {"msg": 2})
            return None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        # Provide a running event loop for run_coroutine_threadsafe
        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.set_event_loop(loop)
        client._callbacks.setdefault("t1", []).append(bad_handler)
        client._callbacks["t1"].append(good_handler)

        client._consumer = consumer
        consumer.subscribe(["t1"])
        client._running = True
        client._consumer_thread = threading.Thread(
            target=client._consume_loop, daemon=True
        )
        client._consumer_thread.start()

        import time
        time.sleep(0.5)

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()
        assert len(received) == 2


# ---------------------------------------------------------------------------
# At-least-once commit semantics (Sub 3.0)
# ---------------------------------------------------------------------------

class TestAtLeastOnceCommit:
    """Sub 3.0: ``enable.auto.commit=False``; commit only after handler success.

    Guards the Executor's at-least-once contract — a callback exception must
    leave the offset uncommitted so Kafka redelivers on rebalance/restart.
    """

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_auto_commit_disabled_in_consumer_config(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        assert client._consumer_conf["enable.auto.commit"] is False

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_commit_after_successful_async_handler(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        received = []

        async def handler(msg):
            received.append(msg)

        consumer = MagicMock()
        msg = _make_mock_message("t1", {"v": 1})
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            return msg if poll_count == 1 else None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.set_event_loop(loop)
        client.subscribe_to_topic("t1", handler)

        import time
        time.sleep(0.5)

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()

        assert received == [{"v": 1}]
        consumer.commit.assert_called_once()
        _, kwargs = consumer.commit.call_args
        assert kwargs.get("message") is msg
        assert kwargs.get("asynchronous") is False

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_no_commit_when_async_handler_raises(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        async def bad_handler(msg):
            raise RuntimeError("boom")

        consumer = MagicMock()
        msg = _make_mock_message("t1", {"v": 1})
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            return msg if poll_count == 1 else None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.set_event_loop(loop)
        client.subscribe_to_topic("t1", bad_handler)

        import time
        time.sleep(0.5)

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()

        consumer.commit.assert_not_called()

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_no_commit_when_sync_handler_raises(self, MockConsumer, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        def bad_handler(msg):
            raise RuntimeError("boom")

        consumer = MagicMock()
        msg = _make_mock_message("t1", {"v": 1})
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            return msg if poll_count == 1 else None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.subscribe_to_topic("t1", bad_handler)

        import time
        time.sleep(0.3)

        client.close()
        consumer.commit.assert_not_called()

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_commit_past_poison_pill(self, MockConsumer, MockProducer):
        """Deserialization errors cannot be fixed by redelivery — must commit past."""
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        consumer = MagicMock()
        bad_msg = MagicMock()
        bad_msg.topic.return_value = "t1"
        bad_msg.value.return_value = b"not-json{{"
        bad_msg.error.return_value = None
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            return bad_msg if poll_count == 1 else None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.subscribe_to_topic("t1", MagicMock())

        import time
        time.sleep(0.3)

        client.close()
        consumer.commit.assert_called_once()
        _, kwargs = consumer.commit.call_args
        assert kwargs.get("message") is bad_msg

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer")
    def test_async_handler_awaited_before_commit(self, MockConsumer, MockProducer):
        """Commit must not happen until the async handler coroutine finishes."""
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        gate = threading.Event()
        completed = threading.Event()

        async def slow_handler(msg):
            # Wait for the test to gate us; commit must not yet have fired.
            while not gate.is_set():
                await asyncio.sleep(0.01)
            completed.set()

        consumer = MagicMock()
        msg = _make_mock_message("t1", {"v": 1})
        poll_count = 0

        def fake_poll(timeout=1.0):
            nonlocal poll_count
            poll_count += 1
            return msg if poll_count == 1 else None

        consumer.poll.side_effect = fake_poll
        MockConsumer.return_value = consumer

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.set_event_loop(loop)
        client.subscribe_to_topic("t1", slow_handler)

        import time
        # Handler is blocked on the gate; commit must NOT have been called.
        time.sleep(0.2)
        assert consumer.commit.call_count == 0

        # Release the handler; poll thread should now commit.
        gate.set()
        assert completed.wait(timeout=2.0)
        time.sleep(0.2)
        assert consumer.commit.call_count == 1

        client.close()
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()


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
        assert client._consumer is None
        consumer.close.assert_called_once()
        producer.flush.assert_called_with(timeout=10)

    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Producer")
    @patch("app.layer4_frameworks.messaging.kafka_broker_client.Consumer", MagicMock())
    def test_close_without_subscribing(self, MockProducer):
        from app.layer4_frameworks.messaging.kafka_broker_client import KafkaBrokerClient

        producer = MagicMock()
        MockProducer.return_value = producer

        client = KafkaBrokerClient(bootstrap_servers="localhost:9092")
        client.close()

        assert client._consumer is None
        producer.flush.assert_called_with(timeout=10)


# ---------------------------------------------------------------------------
# KafkaEventPublisher tests
# ---------------------------------------------------------------------------

class TestKafkaEventPublisher:

    def test_publish_dataclass_event(self):
        from app.layer4_frameworks.messaging.kafka_event_publisher import KafkaEventPublisher
        from app.layer1_domain.events import RunStatusChanged

        broker = MagicMock()
        publisher = KafkaEventPublisher(broker)

        event = RunStatusChanged(run_id="r1", workflow_id="wf-1", status="started")
        publisher.publish("run.status", event)

        # First call is the original topic; second may be the centralized workflow_event topic
        assert broker.publish_to_topic.call_count >= 1
        topic, payload = broker.publish_to_topic.call_args_list[0][0]
        assert topic == "run.status"
        assert payload["run_id"] == "r1"
        assert payload["workflow_id"] == "wf-1"

    def test_publish_dict_event(self):
        from app.layer4_frameworks.messaging.kafka_event_publisher import KafkaEventPublisher

        broker = MagicMock()
        publisher = KafkaEventPublisher(broker)

        event_dict = {"action": "test", "value": 42}
        publisher.publish("custom.topic", event_dict)

        assert broker.publish_to_topic.call_count >= 1
        # Phase M.3.4: dict events without run/workflow ids get key=None.
        broker.publish_to_topic.assert_any_call("custom.topic", event_dict, key=None)

    def test_publish_string_event(self):
        from app.layer4_frameworks.messaging.kafka_event_publisher import KafkaEventPublisher

        broker = MagicMock()
        publisher = KafkaEventPublisher(broker)

        publisher.publish("string.topic", "some-string-event")

        assert broker.publish_to_topic.call_count >= 1
        topic, payload = broker.publish_to_topic.call_args_list[0][0]
        assert topic == "string.topic"
        assert payload == {"data": "some-string-event"}


# ---------------------------------------------------------------------------
# CallbackPublisher tests
# ---------------------------------------------------------------------------

class TestCallbackPublisher:

    def test_publish_calls_broker(self):
        from app.layer4_frameworks.messaging.callback_publisher import CallbackPublisher

        broker = MagicMock()
        publisher = CallbackPublisher(broker)

        message = {"correlation_id": "c1", "status": "COMPLETED"}
        publisher.publish("callback.topic", message, key="c1")

        broker.publish_to_topic.assert_called_once_with(
            "callback.topic", message, key="c1"
        )

    def test_publish_handles_broker_error(self):
        from app.layer4_frameworks.messaging.callback_publisher import CallbackPublisher

        broker = MagicMock()
        broker.publish_to_topic.side_effect = RuntimeError("broker down")
        publisher = CallbackPublisher(broker)

        # Should not raise — logs error internally
        publisher.publish("callback.topic", {"correlation_id": "c1"}, key="c1")


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
        original_worker_url = settings.WORKER_EXECUTION_URL

        try:
            settings.MESSAGING_MODE = "local"
            settings.WORKER_EXECUTION_URL = ""  # disable HttpTaskDispatcher wrapping

            from bootstrap import build_app_container
            container = build_app_container()

            infra = container["_infra"]
            assert infra.broker is not None
            assert hasattr(infra.broker, "publish_to_topic")
            assert hasattr(infra.broker, "close")
        finally:
            settings.MESSAGING_MODE = original_messaging_mode
            settings.INFRA_MODE = original_infra_mode
            settings.WORKER_EXECUTION_URL = original_worker_url

    def test_bootstrap_mock_mode_no_broker(self):
        from app.layer4_frameworks.config.app_config import settings

        original_messaging_mode = settings.MESSAGING_MODE
        original_infra_mode = settings.INFRA_MODE
        original_em_config_source = settings.EVENT_MESH_CONFIG_SOURCE

        try:
            settings.MESSAGING_MODE = ""
            settings.INFRA_MODE = "mock"
            # Neutralise the env-source override: a developer running locally
            # may have a complete EVENT_MESH_* credential set in their
            # environment, which promotes Event Mesh to the primary broker
            # regardless of INFRA_MODE (see _create_message_broker_client).
            # Forcing the source to "" isolates this test from the ambient
            # environment so it verifies the genuine nothing-configured path.
            settings.EVENT_MESH_CONFIG_SOURCE = ""

            from bootstrap import build_app_container
            container = build_app_container()

            infra = container["_infra"]
            assert infra.broker is None
        finally:
            settings.MESSAGING_MODE = original_messaging_mode
            settings.INFRA_MODE = original_infra_mode
            settings.EVENT_MESH_CONFIG_SOURCE = original_em_config_source
