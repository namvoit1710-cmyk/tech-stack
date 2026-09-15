"""G1 — never attach AMQP to a queue that already has consumers.

The queues are NON_EXCLUSIVE, so the broker round-robins across everything
bound to them. Attaching next to a consumer that is no longer fetching means
whatever round-robin hands to that consumer is stranded. Measured on a scratch
queue 2026-08-05: 20 published, the AMQP link received 17, and 3 sat `unacked`
forever. A run only needs its StartRunAction stranded to hang — which is what
the three tenant-1 AMQP outages were.

P1 already makes the collision structurally unlikely (REST and AMQP no longer
share a queue). G1 is the net under it, for anything else that binds: a
previous pod, another app, a stale flow.

The second half of this file guards a subtler trap: when AMQP is refused, the
REST fallback must poll the REST-NAMED queue. Polling the AMQP queue would
create the very 3 consumer flows that strand the next attach — a fallback that
quietly arms the bug it is recovering from.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging import event_mesh_consumer as emc
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig

NAMESPACE = "simplemdg/event/core-2"
TOPIC = "orchestration.actions"
REST_Q = f"{NAMESPACE}/sub-orchestration-actions"
AMQP_Q = f"{REST_Q}-amqp"


def _make_client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace=NAMESPACE,
        consume_transport="amqp",
        amqp_url="wss://em.example.com/protocols/amqp10ws",
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


class TestConsumerCountRead:
    def test_it_reads_the_live_count(self):
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"consumerCount": 3}
        with patch.object(client._admin, "_management_get", return_value=resp):
            assert client._admin.consumer_count(REST_Q) == 3

    def test_a_virgin_queue_reads_zero(self):
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"consumerCount": 0}
        with patch.object(client._admin, "_management_get", return_value=resp):
            assert client._admin.consumer_count(REST_Q) == 0

    @pytest.mark.parametrize("resp", [None, MagicMock(status_code=404)])
    def test_unreadable_is_none_not_zero(self, resp):
        """None and 0 must not be confused — one means 'unknown', the other
        means 'safe to attach'."""
        client = _make_client()
        with patch.object(client._admin, "_management_get", return_value=resp):
            assert client._admin.consumer_count(REST_Q) is None

    def test_a_missing_field_is_none(self):
        client = _make_client()
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"messageCount": 0}
        with patch.object(client._admin, "_management_get", return_value=resp):
            assert client._admin.consumer_count(REST_Q) is None


class TestAttachGuard:
    def test_a_free_queue_is_attached(self):
        client = _make_client()
        with patch.object(client._admin, "consumer_count", return_value=0):
            assert client._consumer._queue_is_free(AMQP_Q) is True

    def test_a_queue_with_consumers_is_refused(self):
        client = _make_client()
        with patch.object(client._admin, "consumer_count", return_value=3):
            assert client._consumer._queue_is_free(AMQP_Q) is False

    def test_one_other_consumer_is_enough_to_refuse(self):
        client = _make_client()
        with patch.object(client._admin, "consumer_count", return_value=1):
            assert client._consumer._queue_is_free(AMQP_Q) is False

    @pytest.mark.parametrize("count", [None, "3", MagicMock()])
    def test_anything_but_a_definite_count_does_not_stop_consumption(self, count):
        """Refuse only on positive knowledge. A management-API blip must not
        be worse than the bug the guard exists to prevent."""
        client = _make_client()
        with patch.object(client._admin, "consumer_count", return_value=count):
            assert client._consumer._queue_is_free(AMQP_Q) is True

    def test_the_guard_runs_before_the_probe_not_just_before_attach(self):
        """probe() attaches a real receiver link and grants credit, so a busy
        queue can strand messages during the probe itself.

        ``amqp_available`` must be forced: ``proton`` is absent from the test
        environment, so without it this returns at the libs check and never
        reaches the guard. It passed anyway while both paths returned a bare
        ``False`` — a distinct reason per cause is what exposed it.
        """
        client = _make_client()
        with patch.object(client._admin, "consumer_count", return_value=3), \
             patch(
                 "app.layer4_frameworks.messaging.event_mesh_amqp_consumer"
                 ".amqp_available", return_value=True,
             ), \
             patch(
                 "app.layer4_frameworks.messaging.event_mesh_amqp_consumer"
                 ".EventMeshAMQPConsumer"
             ) as amqp_cls:
            assert client._consumer._start_amqp_consumer(AMQP_Q) == emc.ATTACH_PEER
        amqp_cls.assert_not_called()

    def test_the_refusal_is_recorded_as_a_fallback(self):
        client = _make_client()
        with patch.object(client._admin, "consumer_count", return_value=3), \
             patch.object(client._consumer._metrics, "record_consume_fallback") as rec:
            client._consumer._queue_is_free(AMQP_Q)
        rec.assert_called_once_with("queue_has_consumers")

    def test_the_guard_runs_before_a_queue_joins_a_live_consumer(self):
        """The second attach path — an already-built AMQP consumer — is the
        one that adds queues 2..N, so it needs the guard too."""
        client = _make_client()
        fake = MagicMock()
        client._consumer._amqp_state = "amqp"
        client._consumer._amqp_consumer = fake
        with patch.object(client._admin, "consumer_count", return_value=3):
            assert client._consumer._start_amqp_consumer(AMQP_Q) == emc.ATTACH_PEER
        fake.add_queue.assert_not_called()

    def test_a_free_queue_still_joins_a_live_consumer(self):
        client = _make_client()
        fake = MagicMock()
        client._consumer._amqp_state = "amqp"
        client._consumer._amqp_consumer = fake
        with patch.object(client._admin, "consumer_count", return_value=0):
            assert client._consumer._start_amqp_consumer(AMQP_Q) == emc.ATTACH_OK
        fake.add_queue.assert_called_once_with(AMQP_Q)


class TestFallbackNeverPoisonsTheAmqpQueue:
    """Guards the shape of the fallback WHEN IT RUNS.

    Since the tenant-1 reproduction on 2026-08-11 it only runs behind
    ``EVENT_MESH_RUNTIME_FALLBACK`` — automatically switching transport is what
    re-created five retired subscriptions and duplicated every message on them.
    The invariant these tests hold is still worth holding: if the fallback runs
    at all, it must land on the REST-named queue and never poll the AMQP one.
    Default-off behaviour is pinned in ``test_event_mesh_no_runtime_fallback``.
    """

    def test_a_refused_attach_falls_back_to_the_REST_named_queue(self):
        """Polling the AMQP queue would create the 3 consumer flows that
        strand the next attach — the fallback would arm the bug it recovers
        from."""
        client = _make_client(runtime_fallback=True)
        with patch.object(client._admin, "provision_subscription"), \
             patch.object(client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_PEER), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert list(client._consumer._callbacks) == [REST_Q]
        assert AMQP_Q not in client._consumer._callbacks
        client.close()

    def test_a_successful_attach_uses_only_the_amqp_queue(self):
        client = _make_client()
        with patch.object(client._admin, "provision_subscription"), \
             patch.object(client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_OK):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert list(client._consumer._callbacks) == [AMQP_Q]
        client.close()

    def test_the_rest_queue_is_provisioned_only_on_fallback(self):
        client = _make_client()
        provisioned: list[str] = []
        with patch.object(client._admin, "provision_subscription",
                          side_effect=lambda q, _t: provisioned.append(q)), \
             patch.object(client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_OK):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert provisioned == ["sub-orchestration-actions-amqp"]
        client.close()

    def test_fallback_provisions_both_and_polls_the_rest_one(self):
        client = _make_client(runtime_fallback=True)
        provisioned: list[str] = []
        with patch.object(client._admin, "provision_subscription",
                          side_effect=lambda q, _t: provisioned.append(q)), \
             patch.object(client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_PEER), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert provisioned == [
            "sub-orchestration-actions-amqp",
            "sub-orchestration-actions",
        ]
        # SA-2319 replaced the single ``_consumer_thread`` with one thread PER
        # QUEUE. Naming the queue is also what this test was always about — the
        # old ``is not None`` could not tell "polls the REST one" from "polls
        # the AMQP one", which is the very mistake the class is named for.
        threads = client._consumer._consumer_threads
        assert REST_Q in threads and threads[REST_Q] is not None
        assert AMQP_Q not in threads, "fell back onto the AMQP queue"
        client.close()

    def test_rest_configured_never_touches_an_amqp_queue(self):
        client = _make_client(consume_transport="rest")
        provisioned: list[str] = []
        with patch.object(client._admin, "provision_subscription",
                          side_effect=lambda q, _t: provisioned.append(q)), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert provisioned == ["sub-orchestration-actions"]
        assert list(client._consumer._callbacks) == [REST_Q]
        client.close()


class TestTheHandlerExistsBeforeTheLinkDoes:
    """`_start_amqp_consumer` probes and then attaches a receiver with real
    credit, so the broker can deliver before it returns. Registering the
    callback afterwards leaves a window in which a delivered message finds an
    empty handler list.

    Measured on tenant-1 2026-08-05 (`solace-971`), from the startup log:

        AMQP receiver link OPEN  (sub-orchestration-actions-amqp)
        AMQP FIRST delivery      (sub-orchestration-actions-amqp)
        No handler registered … message will be ACKED and DISCARDED   ×6

    Six `orchestration.actions` messages destroyed on one startup. On that
    queue a discarded StartRunAction is a permanently hung run — the exact
    outage signature the AMQP work started from. REST never had the window: it
    registers before starting its poll loop.
    """

    def test_the_callback_is_registered_before_the_consumer_starts(self):
        client = _make_client()
        cb = lambda _d: None
        seen_at_start = {}

        def _spy(queue_name):
            seen_at_start["handlers"] = list(
                client._consumer._callbacks.get(queue_name, [])
            )
            return emc.ATTACH_OK

        with patch.object(client._admin, "provision_subscription"), \
             patch.object(client._consumer, "_start_amqp_consumer", side_effect=_spy):
            client._consumer.subscribe(TOPIC, cb)

        assert seen_at_start["handlers"] == [cb], (
            "the handler must be registered BEFORE the link can deliver — "
            "anything delivered into an empty list is lost"
        )

    def test_a_refused_queue_does_not_keep_the_handler(self):
        """Nothing consumes the AMQP queue after a refusal, so a callback left
        registered against it is a lie the next reader has to untangle.

        Default-off: the callback goes nowhere at all — the topic is stood
        down, and the retry re-registers it if the peer ever releases the
        queue. Registering it against the REST queue is precisely the
        improvisation that duplicated five topics on tenant-1."""
        client = _make_client()
        cb = lambda _d: None

        with patch.object(client._admin, "provision_subscription"), \
             patch.object(client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_PEER), \
             patch.object(client._consumer, "_poll_loop"):
            client._consumer.subscribe(TOPIC, cb)

        assert AMQP_Q not in client._consumer._callbacks
        assert REST_Q not in client._consumer._callbacks
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_PEER}

    def test_a_refused_queue_hands_the_callback_to_rest_only_with_the_hatch_on(self):
        client = _make_client(runtime_fallback=True)
        cb = lambda _d: None

        with patch.object(client._admin, "provision_subscription"), \
             patch.object(client._consumer, "_start_amqp_consumer", return_value=emc.ATTACH_PEER), \
             patch.object(client._consumer, "_poll_loop"):
            client._consumer.subscribe(TOPIC, cb)

        assert AMQP_Q not in client._consumer._callbacks
        assert client._consumer._callbacks.get(REST_Q) == [cb]

    def test_unregister_leaves_other_handlers_alone(self):
        client = _make_client()
        keep, drop = (lambda _d: None), (lambda _d: None)
        client._consumer._callbacks[AMQP_Q] = [keep, drop]

        client._consumer._unregister(AMQP_Q, drop)

        assert client._consumer._callbacks[AMQP_Q] == [keep]
