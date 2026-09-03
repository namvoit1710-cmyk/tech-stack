"""Tests for the messaging factory and related transport adapters."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.helpers.testing import StubLogger as _StubLogger


def _assert_buffered_notifier(result, expected_inner_type):
    from agent_sdk.layer4_frameworks.messaging.buffered_push_gateway_notifier import (
        BufferedPushGatewayNotifier,
    )

    assert isinstance(result, BufferedPushGatewayNotifier)
    assert isinstance(result._inner, expected_inner_type)


# ---------------------------------------------------------------------------
# BrokerClient protocol surface
# ---------------------------------------------------------------------------


def test_kafka_broker_client_has_expected_surface():
    import inspect

    from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
        KafkaBrokerClient,
    )

    methods = {
        name
        for name, _ in inspect.getmembers(
            KafkaBrokerClient, predicate=inspect.isfunction
        )
    }
    assert "publish_to_topic" in methods
    assert "subscribe_to_topic" in methods
    assert "close" in methods


def test_event_mesh_broker_client_has_expected_surface():
    import inspect

    from agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client import (
        EventMeshBrokerClient,
    )

    methods = {
        name
        for name, _ in inspect.getmembers(
            EventMeshBrokerClient, predicate=inspect.isfunction
        )
    }
    assert "publish_to_topic" in methods
    assert "subscribe_to_topic" in methods
    assert "close" in methods


# ---------------------------------------------------------------------------
# MockMessageConsumer
# ---------------------------------------------------------------------------


def test_mock_consumer_implements_start_stop():
    from agent_sdk.layer4_frameworks.messaging.mock_consumer import MockMessageConsumer

    consumer = MockMessageConsumer(logger=_StubLogger())
    assert asyncio.run(consumer.start(lambda msg: None)) is None
    assert asyncio.run(consumer.stop()) is None


def test_mock_consumer_does_not_invoke_handler():
    from agent_sdk.layer4_frameworks.messaging.mock_consumer import MockMessageConsumer

    calls = []
    consumer = MockMessageConsumer(logger=_StubLogger())
    asyncio.run(consumer.start(lambda msg: calls.append(msg)))
    assert calls == []


# ---------------------------------------------------------------------------
# EventMesh publisher/consumer adapters
# ---------------------------------------------------------------------------


def test_event_mesh_publisher_publish_calls_broker():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_publisher import (
        EventMeshMessagePublisher,
    )

    class _FakeBroker:
        def __init__(self):
            self.calls = []

        def publish_to_topic(self, topic, message, key=None):
            self.calls.append((topic, message, key))

    broker = _FakeBroker()
    publisher = EventMeshMessagePublisher(broker_client=broker, logger=_StubLogger())
    asyncio.run(publisher.publish("my.topic", {"hello": "world"}, key="k1"))
    assert len(broker.calls) == 1
    assert broker.calls[0] == ("my.topic", {"hello": "world"}, "k1")


def test_event_mesh_consumer_start_subscribes_to_broker():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    class _FakeBroker:
        def __init__(self):
            self.subscriptions = []

        def subscribe_to_topic(self, topic, callback):
            self.subscriptions.append((topic, callback))

    broker = _FakeBroker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=_StubLogger(),
    )

    async def _handler(msg):
        pass

    asyncio.run(consumer.start(_handler))
    assert len(broker.subscriptions) == 1
    assert broker.subscriptions[0][0] == "agent.request"


def test_event_mesh_consumer_decodes_json_string_inbound():
    """String-encoded JSON messages must be decoded before delivery to handler."""
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    delivered = []

    class _FakeBroker:
        def __init__(self):
            self._callbacks = {}

        def subscribe_to_topic(self, topic, callback):
            self._callbacks[topic] = callback

        async def trigger(self, topic, payload):
            cb = self._callbacks[topic]
            if asyncio.iscoroutinefunction(cb):
                await cb(payload)
            else:
                cb(payload)

    broker = _FakeBroker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=_StubLogger(),
    )

    async def _handler(msg):
        delivered.append(msg)

    async def _run():
        await consumer.start(_handler)
        raw = json.dumps({"correlation_id": "c1", "message": "hi"})
        await broker.trigger("agent.request", raw)

    asyncio.run(_run())
    assert len(delivered) == 1
    assert delivered[0].payload["correlation_id"] == "c1"
    assert delivered[0].payload["message"] == "hi"
    assert isinstance(delivered[0].payload, dict)


# ---------------------------------------------------------------------------
# create_messaging factory — mock mode
# ---------------------------------------------------------------------------


def test_factory_mock_mode_returns_console_publisher_and_mock_consumer():
    from agent_sdk.layer4_frameworks.messaging.console_publisher import (
        ConsoleMessagePublisher,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import create_messaging
    from agent_sdk.layer4_frameworks.messaging.mock_consumer import MockMessageConsumer

    class _Settings:
        MESSAGING_MODE = "mock"
        INFRA_MODE = "mock"

    result = create_messaging(_Settings(), _StubLogger())
    assert isinstance(result.publisher, ConsoleMessagePublisher)
    assert isinstance(result.consumer, MockMessageConsumer)
    assert result.backend_name == "mock"


# ---------------------------------------------------------------------------
# create_messaging factory — local (Kafka) mode
# ---------------------------------------------------------------------------


def test_factory_local_mode_returns_kafka_publisher_and_consumer():
    from agent_sdk.layer4_frameworks.messaging.factory import create_messaging
    from agent_sdk.layer4_frameworks.messaging.kafka_consumer import (
        KafkaMessageConsumer,
    )
    from agent_sdk.layer4_frameworks.messaging.kafka_publisher import (
        KafkaMessagePublisher,
    )

    class _FakeBroker:
        def publish_to_topic(self, topic, message, key=None):
            pass

        def subscribe_to_topic(self, topic, callback):
            pass

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        KAFKA_BOOTSTRAP_SERVERS = "localhost:9092"
        KAFKA_REQUEST_TOPIC = "agent.request"
        KAFKA_GROUP_ID = "agent-sdk-consumer"

    fake_broker = _FakeBroker()
    result = create_messaging(_Settings(), _StubLogger(), broker=fake_broker)
    assert isinstance(result.publisher, KafkaMessagePublisher)
    assert isinstance(result.consumer, KafkaMessageConsumer)
    assert result.backend_name == "kafka"


# ---------------------------------------------------------------------------
# create_messaging factory — sap (Event Mesh) mode
# ---------------------------------------------------------------------------


def test_factory_sap_mode_returns_event_mesh_publisher_and_consumer():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )
    from agent_sdk.layer4_frameworks.messaging.event_mesh_publisher import (
        EventMeshMessagePublisher,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import create_messaging

    class _FakeBroker:
        def publish_to_topic(self, topic, message, key=None):
            pass

        def subscribe_to_topic(self, topic, callback):
            pass

    class _Settings:
        MESSAGING_MODE = "sap"
        INFRA_MODE = "mock"
        KAFKA_REQUEST_TOPIC = "agent.request"
        KAFKA_GROUP_ID = "agent-sdk-consumer"

    fake_broker = _FakeBroker()
    result = create_messaging(_Settings(), _StubLogger(), broker=fake_broker)
    assert isinstance(result.publisher, EventMeshMessagePublisher)
    assert isinstance(result.consumer, EventMeshMessageConsumer)
    assert result.backend_name == "event_mesh"


# ---------------------------------------------------------------------------
# create_messaging factory — INFRA_MODE=mock fallback
# ---------------------------------------------------------------------------


def test_factory_infra_mock_overrides_local_to_mock():
    """When INFRA_MODE=mock and no broker provided, return mock regardless of MESSAGING_MODE."""
    from agent_sdk.layer4_frameworks.messaging.console_publisher import (
        ConsoleMessagePublisher,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import create_messaging
    from agent_sdk.layer4_frameworks.messaging.mock_consumer import MockMessageConsumer

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"

    result = create_messaging(_Settings(), _StubLogger())
    assert isinstance(result.publisher, ConsoleMessagePublisher)
    assert isinstance(result.consumer, MockMessageConsumer)
    assert result.backend_name == "mock"


# ---------------------------------------------------------------------------
# Settings — PUSH_GATEWAY_URL field
# ---------------------------------------------------------------------------


def test_settings_exposes_push_gateway_url_with_empty_default():
    """Settings must expose PUSH_GATEWAY_URL with a default of empty string."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert hasattr(s, "PUSH_GATEWAY_URL")
    assert s.PUSH_GATEWAY_URL == ""


def test_settings_exposes_push_gateway_transport_with_grpc_default():
    """Settings must default PUSH_GATEWAY_TRANSPORT to grpc."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert hasattr(s, "PUSH_GATEWAY_TRANSPORT")
    assert s.PUSH_GATEWAY_TRANSPORT == "grpc"


def test_settings_exposes_push_gateway_grpc_target_with_empty_default():
    """Settings must expose PUSH_GATEWAY_GRPC_TARGET with a default of empty string."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings()
    assert hasattr(s, "PUSH_GATEWAY_GRPC_TARGET")
    assert s.PUSH_GATEWAY_GRPC_TARGET == ""


def test_settings_normalizes_push_gateway_transport_to_lowercase():
    """Settings should normalize PUSH_GATEWAY_TRANSPORT to lowercase."""
    from agent_sdk.layer4_frameworks.config.app_config import Settings

    s = Settings(PUSH_GATEWAY_TRANSPORT="Grpc")
    assert s.PUSH_GATEWAY_TRANSPORT == "grpc"


# ---------------------------------------------------------------------------
# build_push_gateway_notifier factory
# ---------------------------------------------------------------------------


def test_build_push_gateway_notifier_mock_mode_returns_console_notifier():
    """Resolved mode=mock returns ConsolePushGatewayNotifier."""
    from agent_sdk.layer4_frameworks.messaging.console_push_gateway_notifier import (
        ConsolePushGatewayNotifier,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    class _Settings:
        MESSAGING_MODE = "mock"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "grpc"
        PUSH_GATEWAY_URL = ""

    result = build_push_gateway_notifier(_Settings())
    assert isinstance(result, ConsolePushGatewayNotifier)


def test_build_push_gateway_notifier_non_mock_with_url_returns_http_notifier():
    """Resolved mode non-mock with PUSH_GATEWAY_URL returns a buffered HTTP notifier."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )
    from agent_sdk.layer4_frameworks.messaging.http_push_gateway_notifier import (
        HttpPushGatewayNotifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "http"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"

    result = build_push_gateway_notifier(_Settings())
    _assert_buffered_notifier(result, HttpPushGatewayNotifier)


def test_build_push_gateway_notifier_non_mock_http_transport_with_url_returns_http_notifier():
    """Explicit http transport must preserve buffered URL-based HTTP notifier behavior."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )
    from agent_sdk.layer4_frameworks.messaging.http_push_gateway_notifier import (
        HttpPushGatewayNotifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "http"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"
        PUSH_GATEWAY_GRPC_TARGET = "push-gateway:50051"

    result = build_push_gateway_notifier(_Settings())
    _assert_buffered_notifier(result, HttpPushGatewayNotifier)


def test_build_push_gateway_notifier_non_mock_default_grpc_transport_with_target_returns_grpc_notifier():
    """Default transport should resolve to buffered gRPC when a gRPC target is configured."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )
    from agent_sdk.layer4_frameworks.messaging.grpc_push_gateway_notifier import (
        GrpcPushGatewayNotifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_GRPC_TARGET = "push-gateway:50051"

    result = build_push_gateway_notifier(_Settings())
    _assert_buffered_notifier(result, GrpcPushGatewayNotifier)


def test_build_push_gateway_notifier_non_mock_without_url_returns_noop():
    """Resolved mode non-mock with blank PUSH_GATEWAY_URL returns no-op notifier."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "http"
        PUSH_GATEWAY_URL = ""

    result = build_push_gateway_notifier(_Settings())
    assert not isinstance(result, type(None))
    assert hasattr(result, "send_notification")
    assert hasattr(result, "close")


def test_build_push_gateway_notifier_non_mock_grpc_transport_with_target_returns_grpc_notifier():
    """Explicit grpc transport with a target should return buffered GrpcPushGatewayNotifier."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )
    from agent_sdk.layer4_frameworks.messaging.grpc_push_gateway_notifier import (
        GrpcPushGatewayNotifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "grpc"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"
        PUSH_GATEWAY_GRPC_TARGET = "push-gateway:50051"

    result = build_push_gateway_notifier(_Settings())
    _assert_buffered_notifier(result, GrpcPushGatewayNotifier)


def test_build_push_gateway_notifier_non_mock_grpc_transport_without_target_returns_noop():
    """Explicit grpc transport with no target should preserve no-op behavior."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "grpc"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"
        PUSH_GATEWAY_GRPC_TARGET = ""

    result = build_push_gateway_notifier(_Settings())
    assert not isinstance(result, type(None))
    assert hasattr(result, "send_notification")
    assert hasattr(result, "close")


def test_build_push_gateway_notifier_messaging_mode_local_overrides_infra_mock():
    """MESSAGING_MODE='local' overrides INFRA_MODE='mock': should use buffered URL-based selection."""
    from agent_sdk.layer4_frameworks.messaging.console_push_gateway_notifier import (
        ConsolePushGatewayNotifier,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )
    from agent_sdk.layer4_frameworks.messaging.http_push_gateway_notifier import (
        HttpPushGatewayNotifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "http"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"

    result = build_push_gateway_notifier(_Settings())
    _assert_buffered_notifier(result, HttpPushGatewayNotifier)
    assert not isinstance(result._inner, ConsolePushGatewayNotifier)


def test_build_push_gateway_notifier_invalid_transport_raises_value_error():
    """Unsupported push-gateway transport values must fail predictably."""
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    class _Settings:
        MESSAGING_MODE = "local"
        INFRA_MODE = "mock"
        PUSH_GATEWAY_TRANSPORT = "smtp"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"
        PUSH_GATEWAY_GRPC_TARGET = ""

    with pytest.raises(ValueError, match="Unsupported push gateway transport"):
        build_push_gateway_notifier(_Settings())


def test_build_push_gateway_notifier_mock_mode_ignores_transport_value():
    """Mock mode must continue to return the console notifier regardless of transport."""
    from agent_sdk.layer4_frameworks.messaging.console_push_gateway_notifier import (
        ConsolePushGatewayNotifier,
    )
    from agent_sdk.layer4_frameworks.messaging.factory import (
        build_push_gateway_notifier,
    )

    class _Settings:
        MESSAGING_MODE = "mock"
        INFRA_MODE = "local"
        PUSH_GATEWAY_TRANSPORT = "smtp"
        PUSH_GATEWAY_URL = "http://push-gateway:8080"
        PUSH_GATEWAY_GRPC_TARGET = "push-gateway:50051"

    result = build_push_gateway_notifier(_Settings())
    assert isinstance(result, ConsolePushGatewayNotifier)


def test_event_mesh_consumer_rejects_non_json_messages():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    class _FakeBroker:
        def __init__(self):
            self._callbacks = {}
            self.rejected = []

        def subscribe_to_topic(self, topic, callback):
            self._callbacks[topic] = callback

        def reject_message(self, queue_name, headers, requeue=False):
            self.rejected.append((queue_name, headers, requeue))

        def build_queue_name(self, topic):
            return f"default/{topic}"

        async def trigger(self, topic, payload, headers=None):
            cb = self._callbacks[topic]
            if asyncio.iscoroutinefunction(cb):
                await cb(payload, headers or {})
            else:
                cb(payload, headers or {})

    broker = _FakeBroker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=_StubLogger(),
    )

    delivered = []

    async def _handler(msg):
        delivered.append(msg)

    async def _run():
        await consumer.start(_handler)
        await broker.trigger("agent.request", "not-valid-json", {"msg_id": "1"})

    asyncio.run(_run())
    assert len(delivered) == 0
    assert len(broker.rejected) == 1
    assert broker.rejected[0][2] is False  # requeue=False


def test_event_mesh_consumer_acks_non_json_when_no_reject():
    from agent_sdk.layer4_frameworks.messaging.event_mesh_consumer import (
        EventMeshMessageConsumer,
    )

    class _FakeBroker:
        def __init__(self):
            self._callbacks = {}
            self.acked = []

        def subscribe_to_topic(self, topic, callback):
            self._callbacks[topic] = callback

        def ack_message(self, queue_name, headers):
            self.acked.append((queue_name, headers))

        def build_queue_name(self, topic):
            return f"default/{topic}"

        async def trigger(self, topic, payload, headers=None):
            cb = self._callbacks[topic]
            if asyncio.iscoroutinefunction(cb):
                await cb(payload, headers or {})
            else:
                cb(payload, headers or {})

    broker = _FakeBroker()
    consumer = EventMeshMessageConsumer(
        broker_client=broker,
        topic="agent.request",
        logger=_StubLogger(),
    )

    async def _handler(msg):
        pass

    async def _run():
        await consumer.start(_handler)
        await broker.trigger("agent.request", "bad-json", {"msg_id": "2"})

    asyncio.run(_run())
    assert len(broker.acked) == 1
