"""P1 — a queue is identified by (topic, transport, scope), not topic alone.

Why transport is in the name (measured on tenant-1, 2026-08-05):
SAP allocates **3 broker-side consumer flows per REST-polled queue**, and
stopping the poller does not release them — they expire only after 30 minutes
with *no publishing*, which a hot queue never has. Queues are NON_EXCLUSIVE, so
an AMQP link attaching to the same queue becomes a 4th round-robin consumer and
whatever the broker hands to a stopped flow is stranded. Reproduced: 20
published, AMQP received 17, 3 stuck `unacked` forever. A run only needs its
StartRunAction stranded to hang — which is exactly the three tenant-1 outages.

Why scope is in the signature before anything uses it:
work topics want competing consumers; fan-out topics (realtime.events,
catalog.changed) need one queue per instance or instance A eats an event whose
Socket.IO client is on instance B. Adding the dimension later means renaming
live queues a second time, and renaming a live queue is the migration that
caused those outages.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging import event_mesh_consumer as emc
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames

NAMESPACE = "simplemdg/event/core-2"
TOPIC = "orchestration.actions"
BARE = "sub-orchestration-actions"


def _make_client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace=NAMESPACE,
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


class TestQueueLocalFromTopic:
    def test_the_bare_name_is_unchanged(self):
        """REST keeps the historical name so no live queue is renamed."""
        assert EventMeshNames.queue_local_from_topic(TOPIC) == BARE

    @pytest.mark.parametrize("transport", ["rest", "REST", "", None])
    def test_rest_and_empty_never_add_a_suffix(self, transport):
        assert EventMeshNames.queue_local_from_topic(TOPIC, transport) == BARE

    def test_amqp_gets_its_own_queue(self):
        assert (
            EventMeshNames.queue_local_from_topic(TOPIC, "amqp") == f"{BARE}-amqp"
        )

    def test_transport_is_case_insensitive(self):
        assert (
            EventMeshNames.queue_local_from_topic(TOPIC, "AMQP") == f"{BARE}-amqp"
        )

    def test_scope_appends_after_transport(self):
        assert (
            EventMeshNames.queue_local_from_topic(TOPIC, "amqp", "i7")
            == f"{BARE}-amqp-i7"
        )

    def test_scope_works_without_a_transport(self):
        """Fan-out on REST is still one queue per instance."""
        assert (
            EventMeshNames.queue_local_from_topic("realtime.events", None, "i7")
            == "sub-realtime-events-i7"
        )

    def test_dots_are_still_hyphenated(self):
        assert (
            EventMeshNames.queue_local_from_topic("orchestration.responses.7a2c7636")
            == "sub-orchestration-responses-7a2c7636"
        )

    def test_rest_and_amqp_never_collide(self):
        rest = EventMeshNames.queue_local_from_topic(TOPIC, "rest")
        amqp = EventMeshNames.queue_local_from_topic(TOPIC, "amqp")
        assert rest != amqp


class TestTheDerivationIsSharedByEveryPath:
    """Provision, subscribe and delete must never disagree about the name —
    a mismatch means one of them silently operates on a queue nobody reads.
    """

    @pytest.mark.parametrize(
        "transport,expected",
        [("rest", BARE), ("amqp", f"{BARE}-amqp")],
    )
    def test_ensure_topic_provisions_the_queue_the_consumer_will_use(
        self, transport, expected,
    ):
        client = _make_client(
            consume_transport=transport,
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        with patch.object(client._admin, "provision_subscription") as prov:
            client.ensure_topic(TOPIC)
        prov.assert_called_once_with(expected, TOPIC)

    @pytest.mark.parametrize(
        "transport,expected",
        [("rest", BARE), ("amqp", f"{BARE}-amqp")],
    )
    def test_delete_topic_queue_targets_the_same_queue(self, transport, expected):
        client = _make_client(
            consume_transport=transport,
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        with patch.object(client._admin, "delete_queue") as delete:
            client.delete_topic_queue(TOPIC)
        delete.assert_called_once_with(expected)

    @pytest.mark.parametrize(
        "transport,expected",
        [("rest", BARE), ("amqp", f"{BARE}-amqp")],
    )
    def test_subscribe_registers_the_callback_under_the_same_queue(
        self, transport, expected,
    ):
        client = _make_client(
            consume_transport=transport,
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        with patch.object(client._admin, "provision_subscription"), \
             patch.object(
                 client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_OK,
             ), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert list(client._consumer._callbacks) == [f"{NAMESPACE}/{expected}"]
        client.close()

    def test_an_amqp_client_never_touches_the_rest_queue(self):
        """The whole point: the switch cannot land on a queue that still has
        the other transport's consumer flows bound to it."""
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        seen: list[str] = []
        with patch.object(client._admin, "ensure_queue",
                          side_effect=lambda q: seen.append(q)), \
             patch.object(client._admin, "subscription_exists", return_value=True), \
             patch.object(
                 client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_OK,
             ):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert seen == [f"{NAMESPACE}/{BARE}-amqp"]
        assert f"{NAMESPACE}/{BARE}" not in seen
        client.close()


class TestDmqNamingComposesWithTransport:
    def test_the_amqp_queue_gets_its_own_dmq(self):
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        qualified = f"{NAMESPACE}/{BARE}-amqp"
        assert client._admin.dmq_name(qualified) == f"{qualified}-dmq"

    def test_a_dmq_is_still_recognised_as_a_dmq(self):
        """`-amqp-dmq` must not be mistaken for a normal queue, or it would
        get a DMQ of its own."""
        client = _make_client()
        assert client._admin._is_dmq(f"{NAMESPACE}/{BARE}-amqp-dmq")
        assert not client._admin._is_dmq(f"{NAMESPACE}/{BARE}-amqp")
