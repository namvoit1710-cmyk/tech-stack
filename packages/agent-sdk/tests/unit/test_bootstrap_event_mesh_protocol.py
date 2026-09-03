from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

from agent_sdk.bootstrap import _build_broker


def _settings(protocol: str) -> SimpleNamespace:
    return SimpleNamespace(
        MESSAGING_MODE="sap",
        INFRA_MODE="",
        EVENT_MESH_PROTOCOL=protocol,
        EVENT_MESH_MESSAGING_PROTOCOL="",
        EVENT_MESH_MANAGEMENT_PROTOCOL="httprest",
        EVENT_MESH_TOKEN_URL="https://auth.example/oauth/token",
        EVENT_MESH_MESSAGING_URL="https://rest.example",
        EVENT_MESH_MESSAGING_AMQP10WS_URL="wss://amqpws.example",
        EVENT_MESH_MESSAGING_AMQP10_URL="amqps://amqp.example",
        EVENT_MESH_MESSAGING_MQTT311WS_URL="wss://mqttws.example",
        EVENT_MESH_MESSAGING_MQTT311_URL="mqtts://mqtt.example",
        EVENT_MESH_MANAGEMENT_URL="https://management.example",
        EVENT_MESH_BROKER_URL="https://broker.example",
        EVENT_MESH_CLIENT_ID="client-id",
        EVENT_MESH_CLIENT_SECRET="client-secret",
        EVENT_MESH_NAMESPACE="ns1",
        EVENT_MESH_VERIFY_SSL=True,
        EVENT_MESH_REQUEST_TIMEOUT_SECONDS=30.0,
        EVENT_MESH_POLL_INTERVAL_SECONDS=1.0,
        EVENT_MESH_AMQP_AUTH_MODE="oauth2",
        EVENT_MESH_AMQP_TOPIC_ADDRESS_TEMPLATE="topic:{topic_path}",
        EVENT_MESH_AMQP_QUEUE_ADDRESS_TEMPLATE="queue:{queue_path}",
        EVENT_MESH_AMQP_PREFETCH=10,
        EVENT_MESH_AMQP_POLL_TIMEOUT_SECONDS=1.0,
        EVENT_MESH_AMQP_DEBUG=False,
        EVENT_MESH_AMQP_TOKEN_RETRY_ATTEMPTS=3,
        EVENT_MESH_AMQP_TOKEN_RETRY_INITIAL_DELAY_SECONDS=0.2,
        EVENT_MESH_AMQP_TOKEN_RETRY_MAX_DELAY_SECONDS=2.0,
        EVENT_MESH_AMQP_PUBLISH_RETRY_ATTEMPTS=2,
        EVENT_MESH_AMQP_PUBLISH_RETRY_INITIAL_DELAY_SECONDS=0.2,
        EVENT_MESH_AMQP_PUBLISH_RETRY_MAX_DELAY_SECONDS=2.0,
        EVENT_MESH_AMQP_SEND_CLIENT_CACHE_MAX_SIZE=128,
        EVENT_MESH_AMQP_SEND_CLIENT_IDLE_TTL_SECONDS=900.0,
        EVENT_MESH_MQTT_AUTH_MODE="oauth2",
        EVENT_MESH_MQTT_CLIENT_ID="",
        EVENT_MESH_MQTT_TOPIC_TEMPLATE="{topic_path}",
        EVENT_MESH_MQTT_QUEUE_TEMPLATE="{queue_path}",
        EVENT_MESH_MQTT_QOS=1,
        EVENT_MESH_MQTT_KEEPALIVE_SECONDS=60,
        EVENT_MESH_MQTT_CLEAN_SESSION=True,
        EVENT_MESH_MQTT_RECONNECT_RETRIES=3,
        EVENT_MESH_MQTT_RECONNECT_MAX_INTERVAL_SECONDS=10,
        EVENT_MESH_MQTT_CONNECT_RETRY_ATTEMPTS=3,
        EVENT_MESH_MQTT_CONNECT_RETRY_INITIAL_DELAY_SECONDS=0.2,
        EVENT_MESH_MQTT_CONNECT_RETRY_MAX_DELAY_SECONDS=2.0,
        EVENT_MESH_MQTT_PUBLISH_RETRY_ATTEMPTS=2,
        EVENT_MESH_MQTT_PUBLISH_RETRY_INITIAL_DELAY_SECONDS=0.2,
        EVENT_MESH_MQTT_PUBLISH_RETRY_MAX_DELAY_SECONDS=2.0,
        EVENT_MESH_MQTT_LAST_WILL_TOPIC="",
        EVENT_MESH_MQTT_LAST_WILL_MESSAGE="",
        EVENT_MESH_MQTT_LAST_WILL_QOS=1,
        EVENT_MESH_MQTT_LAST_WILL_RETAIN=False,
        CONSUMER_MAX_IN_FLIGHT_MESSAGES=4,
        CONSUMER_SHUTDOWN_GRACE_SECONDS=30,
    )


def _install_fake_broker_module(
    monkeypatch,
    module_name: str,
    class_name: str,
):
    module = ModuleType(module_name)

    class _FakeBroker:
        calls: list[dict[str, Any]] = []

        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = dict(kwargs)
            self.__class__.calls.append(dict(kwargs))

    setattr(module, class_name, _FakeBroker)
    monkeypatch.setitem(sys.modules, module_name, module)
    return _FakeBroker


def test_sap_event_mesh_uses_amqp_broker_for_amqp_protocol(monkeypatch) -> None:
    broker_cls = _install_fake_broker_module(
        monkeypatch,
        "agent_sdk.layer4_frameworks.messaging.event_mesh_amqp_broker_client",
        "EventMeshAMQPBrokerClient",
    )

    broker = _build_broker(_settings("amqp10ws"))

    assert isinstance(broker, broker_cls)
    assert broker.kwargs == {
        "token_url": "https://auth.example/oauth/token",
        "messaging_url": "wss://amqpws.example",
        "management_url": "https://management.example",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "namespace": "ns1",
        "verify_ssl": True,
        "request_timeout_seconds": 30.0,
        "messaging_protocol": "amqp10ws",
        "auth_mode": "oauth2",
        "topic_address_template": "topic:{topic_path}",
        "queue_address_template": "queue:{queue_path}",
        "prefetch": 10,
        "poll_timeout_seconds": 1.0,
        "debug": False,
        "token_retry_attempts": 3,
        "token_retry_initial_delay_seconds": 0.2,
        "token_retry_max_delay_seconds": 2.0,
        "publish_retry_attempts": 2,
        "publish_retry_initial_delay_seconds": 0.2,
        "publish_retry_max_delay_seconds": 2.0,
        "send_client_cache_max_size": 128,
        "send_client_idle_ttl_seconds": 900.0,
        "max_in_flight_messages": 4,
        "shutdown_grace_seconds": 30,
    }


def test_sap_event_mesh_uses_mqtt_broker_for_mqtt_protocol(monkeypatch) -> None:
    broker_cls = _install_fake_broker_module(
        monkeypatch,
        "agent_sdk.layer4_frameworks.messaging.event_mesh_mqtt_broker_client",
        "EventMeshMQTTBrokerClient",
    )

    broker = _build_broker(_settings("mqtt311ws"))

    assert isinstance(broker, broker_cls)
    assert broker.kwargs == {
        "token_url": "https://auth.example/oauth/token",
        "messaging_url": "wss://mqttws.example",
        "management_url": "https://management.example",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "namespace": "ns1",
        "verify_ssl": True,
        "request_timeout_seconds": 30.0,
        "messaging_protocol": "mqtt311ws",
        "auth_mode": "oauth2",
        "mqtt_client_id": "",
        "topic_template": "{topic_path}",
        "queue_template": "{queue_path}",
        "qos": 1,
        "keepalive_seconds": 60,
        "clean_session": True,
        "reconnect_retries": 3,
        "reconnect_max_interval_seconds": 10,
        "connect_retry_attempts": 3,
        "connect_retry_initial_delay_seconds": 0.2,
        "connect_retry_max_delay_seconds": 2.0,
        "publish_retry_attempts": 2,
        "publish_retry_initial_delay_seconds": 0.2,
        "publish_retry_max_delay_seconds": 2.0,
        "last_will_topic": "",
        "last_will_message": "",
        "last_will_qos": 1,
        "last_will_retain": False,
    }
