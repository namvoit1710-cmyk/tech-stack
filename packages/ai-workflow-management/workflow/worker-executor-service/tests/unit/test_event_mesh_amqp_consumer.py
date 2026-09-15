"""Tests for the AMQP push consumer + broker consume-transport selection.

These run WITHOUT ``python-qpid-proton`` installed, so they cover the parts
knowable offline: transport selection, the automatic fall-back to REST polling,
the queue-address builder, and body parsing. The proton wire path itself needs
a live Event Mesh endpoint and is validated in BTP.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amqp_mod
from app.layer4_frameworks.messaging.event_mesh_amqp_consumer import (
    EventMeshAMQPConsumer,
    _parse_body,
)
from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig


def _make_client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace="simplemdg/event/core-2",
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


def _consumer(**overrides) -> EventMeshAMQPConsumer:
    defaults = dict(
        host="em.example.com",
        get_token=lambda: ("tok", 9_999_999_999.0),
        message_handler=lambda q, d: True,
        namespace="simplemdg/event/core-2",
    )
    defaults.update(overrides)
    return EventMeshAMQPConsumer(**defaults)


def _msg(body):
    m = MagicMock()
    m.body = body
    return m


class TestConsumeTransportSelection:
    def test_no_amqp_url_forces_rest(self):
        client = _make_client(consume_transport="amqp")
        assert client._consume_transport == "rest"

    def test_amqp_url_parsed_into_host_port_path(self):
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com:443/protocols/amqp10ws",
        )
        assert client._consume_transport == "amqp"
        assert client._amqp_host == "em.example.com"
        assert client._amqp_port == 443
        assert client._amqp_ws_path == "/protocols/amqp10ws"

    def test_explicit_rest_never_builds_amqp(self):
        client = _make_client(
            consume_transport="rest",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        with patch.object(client, "queue_exists", return_value=True), \
             patch.object(client, "subscription_exists", return_value=True), \
             patch.object(client, "_poll_loop"):
            client.subscribe_to_topic("run.status", lambda m: None)
        assert client._amqp_state is None
        assert client._amqp_consumer is None
        client.close()


class TestAmqpAutoFallbackToRest:
    def test_falls_back_to_rest_when_amqp_libs_missing(self):
        # When the AMQP libs aren't importable, the broker must fall back to the
        # REST poll pool instead of leaving consumption dead. Patch
        # amqp_available so this is deterministic whether or not proton/
        # websocket-client are installed in the test environment.
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        assert client._consume_transport == "amqp"  # host resolved from url
        with patch.object(client, "queue_exists", return_value=True), \
             patch.object(client, "subscription_exists", return_value=True), \
             patch.object(client, "_poll_loop"), \
             patch.object(amqp_mod, "amqp_available", return_value=False):
            client.subscribe_to_topic("run.status", lambda m: None)

        assert client._amqp_state == "rest"
        assert client._amqp_consumer is None
        assert len(client._consumer_pool) > 0  # REST fallback started
        client.close()

    def test_probe_failure_falls_back_and_sticks(self):
        """A failed connect probe commits the process to REST for all queues."""
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        fake_consumer = MagicMock()
        fake_consumer.probe.return_value = False
        with patch.object(client, "queue_exists", return_value=True), \
             patch.object(client, "subscription_exists", return_value=True), \
             patch.object(client, "_poll_loop"), \
             patch(
                 "app.layer4_frameworks.messaging.event_mesh_amqp_consumer."
                 "EventMeshAMQPConsumer",
                 return_value=fake_consumer,
             ), \
             patch(
                 "app.layer4_frameworks.messaging.event_mesh_amqp_consumer."
                 "amqp_available",
                 return_value=True,
             ):
            client.subscribe_to_topic("run.status", lambda m: None)
            client.subscribe_to_topic("node.dispatched", lambda m: None)

        assert client._amqp_state == "rest"
        fake_consumer.probe.assert_called_once()
        fake_consumer.start.assert_not_called()
        client.close()

    def test_probe_success_uses_amqp_and_adds_queues(self):
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        fake_consumer = MagicMock()
        fake_consumer.probe.return_value = True
        with patch.object(client, "queue_exists", return_value=True), \
             patch.object(client, "subscription_exists", return_value=True), \
             patch.object(client, "_poll_loop") as poll, \
             patch(
                 "app.layer4_frameworks.messaging.event_mesh_amqp_consumer."
                 "EventMeshAMQPConsumer",
                 return_value=fake_consumer,
             ), \
             patch(
                 "app.layer4_frameworks.messaging.event_mesh_amqp_consumer."
                 "amqp_available",
                 return_value=True,
             ):
            client.subscribe_to_topic("run.status", lambda m: None)
            client.subscribe_to_topic("node.dispatched", lambda m: None)

        assert client._amqp_state == "amqp"
        fake_consumer.start.assert_called_once()
        assert fake_consumer.add_queue.call_count == 2
        poll.assert_not_called()
        client.close()


class TestProtonConsumerUnit:
    def test_probe_false_without_amqp_libs(self):
        # With the libs unavailable, probe short-circuits to False (no network).
        with patch.object(amqp_mod, "amqp_available", return_value=False):
            assert _consumer().probe("simplemdg/event/core-2/sub-x") is False

    def test_url_built_from_host_port_path(self):
        c = _consumer(host="em.example.com", port=443, ws_path="/protocols/amqp10ws")
        assert c._url() == "wss://em.example.com:443/protocols/amqp10ws"

    def test_source_address_default_qualified(self):
        c = _consumer()
        assert (
            c._source_address("simplemdg/event/core-2/sub-x")
            == "queue:simplemdg/event/core-2/sub-x"
        )

    def test_source_address_strip_namespace(self):
        c = _consumer(strip_namespace=True)
        assert c._source_address("simplemdg/event/core-2/sub-x") == "queue:sub-x"

    def test_token_is_the_oauth_token(self):
        c = _consumer(get_token=lambda: ("bearer-xyz", 123.0))
        assert c._token() == "bearer-xyz"

    def test_parse_body_json_bytes(self):
        assert _parse_body(_msg(b'{"a": 1}')) == {"a": 1}

    def test_parse_body_json_str(self):
        assert _parse_body(_msg('{"a": 2}')) == {"a": 2}

    def test_parse_body_dict_passthrough(self):
        assert _parse_body(_msg({"a": 3})) == {"a": 3}

    def test_parse_body_raw_fallback(self):
        assert _parse_body(_msg(b"not json")) == {"raw": "not json"}

    def test_parse_body_memoryview(self):
        assert _parse_body(_msg(memoryview(b'{"a": 4}'))) == {"a": 4}
