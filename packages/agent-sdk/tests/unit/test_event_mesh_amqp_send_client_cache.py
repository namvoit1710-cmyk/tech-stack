from __future__ import annotations

from typing import Any

from agent_sdk.layer4_frameworks.messaging.event_mesh_amqp_broker_client import (
    EventMeshAMQPBrokerClient,
)


class _FakeProperties:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeMessage:
    def __init__(self, *, data: list[bytes], properties: Any = None) -> None:
        self.data = data
        self.properties = properties


class _FakeSendClient:
    instances: list["_FakeSendClient"] = []
    fail_next_send = False

    def __init__(self, hostname: str, target: str, **kwargs: Any) -> None:
        self.hostname = hostname
        self.target = target
        self.kwargs = kwargs
        self.sent: list[_FakeMessage] = []
        self.closed = False
        type(self).instances.append(self)

    def send_message(self, message: _FakeMessage, *, timeout: float = 0) -> None:
        if type(self).fail_next_send:
            type(self).fail_next_send = False
            raise RuntimeError("send failed")
        self.sent.append(message)

    def close(self) -> None:
        self.closed = True


def _client() -> EventMeshAMQPBrokerClient:
    client = EventMeshAMQPBrokerClient(
        token_url="https://token.example.com",
        messaging_url="wss://messaging.example.com/protocols/amqp10ws",
        management_url="",
        client_id="client-id",
        client_secret="client-secret",
        namespace="ns",
    )
    client._import_pyamqp = lambda: (
        object,
        _FakeSendClient,
        object,
        object,
        (_FakeMessage, _FakeProperties),
    )
    client._client_kwargs = lambda: {}
    client._hostname = lambda: "messaging.example.com:443"
    # Keep these sender-cache tests fully hermetic. The production AMQP client
    # exports/refreshes OAuth bearer auth before creating/retrying SendClient,
    # but these tests only verify sender caching and retry behavior.
    client._get_access_token = lambda *, force_refresh=False: "unit-test-token"
    client._export_amqp_ws_authorization_for_vendor_transport = (
        lambda *, force_refresh=False: None
    )
    client._request_timeout_seconds = 12.0
    return client


def setup_function() -> None:
    _FakeSendClient.instances.clear()
    _FakeSendClient.fail_next_send = False


def test_amqp_publish_reuses_cached_send_client_for_same_topic() -> None:
    client = _client()
    try:
        client.publish_to_topic("agent.request", {"n": 1}, key="corr-1")
        client.publish_to_topic("agent.request", {"n": 2}, key="corr-2")

        assert len(_FakeSendClient.instances) == 1
        sender = _FakeSendClient.instances[0]
        assert sender.hostname == "messaging.example.com:443"
        assert sender.target == "topic:ns/agent.request"
        assert len(sender.sent) == 2
        assert sender.sent[0].properties.kwargs == {"correlation_id": "corr-1"}
        assert sender.sent[1].properties.kwargs == {"correlation_id": "corr-2"}
        assert not sender.closed
    finally:
        client.close()


def test_amqp_publish_uses_separate_cached_send_client_per_topic() -> None:
    client = _client()
    try:
        client.publish_to_topic("agent.request", {"n": 1})
        client.publish_to_topic("agent.response", {"n": 2})

        assert [item.target for item in _FakeSendClient.instances] == [
            "topic:ns/agent.request",
            "topic:ns/agent.response",
        ]
    finally:
        client.close()


def test_amqp_publish_discards_failed_cached_sender_and_retries_once() -> None:
    client = _client()
    try:
        client.publish_to_topic("agent.request", {"n": 1})
        first_sender = _FakeSendClient.instances[0]
        _FakeSendClient.fail_next_send = True

        client.publish_to_topic("agent.request", {"n": 2})

        assert len(_FakeSendClient.instances) == 2
        assert first_sender.closed
        assert len(_FakeSendClient.instances[1].sent) == 1
    finally:
        client.close()


def test_amqp_publish_evicts_lru_cached_send_client_when_cache_limit_exceeded() -> None:
    client = _client()
    client._send_client_cache_max_size = 2
    try:
        client.publish_to_topic("agent.request.1", {"n": 1})
        client.publish_to_topic("agent.request.2", {"n": 2})
        client.publish_to_topic("agent.request.1", {"n": 3})
        second_sender = _FakeSendClient.instances[1]

        client.publish_to_topic("agent.request.3", {"n": 4})

        assert len(_FakeSendClient.instances) == 3
        assert second_sender.closed
        assert list(client._send_clients.keys()) == [
            "topic:ns/agent.request.1",
            "topic:ns/agent.request.3",
        ]
        assert client._send_client_last_used.keys() == client._send_clients.keys()
    finally:
        client.close()


def test_amqp_publish_evicts_idle_cached_send_clients() -> None:
    client = _client()
    client._send_client_idle_ttl_seconds = 0.001
    try:
        client.publish_to_topic("agent.request.1", {"n": 1})
        first_sender = _FakeSendClient.instances[0]
        import time

        time.sleep(0.01)
        client.publish_to_topic("agent.request.2", {"n": 2})

        assert first_sender.closed
        assert list(client._send_clients.keys()) == ["topic:ns/agent.request.2"]
    finally:
        client.close()


def test_amqp_close_closes_cached_send_clients() -> None:
    client = _client()

    client.publish_to_topic("agent.request", {"n": 1})
    client.publish_to_topic("agent.response", {"n": 2})
    senders = list(_FakeSendClient.instances)

    client.close()

    assert senders
    assert all(sender.closed for sender in senders)
    assert client._send_clients == {}
