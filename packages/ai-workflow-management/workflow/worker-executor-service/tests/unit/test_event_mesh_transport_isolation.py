"""The executor's task queue is one queue shared by two transports.

Measured on tenant-1 2026-08-05, live:

    sub-workflow.tasks    messageCount=0  consumerCount=3  maxRedeliveryCount=0
                          deadMsgQueue=None

Two separate defects sit in that one line.

**consumerCount = 3.** Those are the executor's own REST poll flows: SAP
allocates 3 broker-side flows per REST-polled queue and releases them only
after 30 minutes with *no publishing*, which a task queue never has. The queue
is NON_EXCLUSIVE, so an AMQP link attaching to the same name becomes a 4th
round-robin consumer, and whatever the broker hands a stopped flow is stranded
(reproduced: 20 published, 17 delivered, 3 unacked forever). On the control
plane that hung runs three times. Here it would be a dispatched task that
never runs.

**maxRedeliveryCount = 0, no deadMsgQueue.** `create_queue` was a bare PUT with
no body, so the queue has no redelivery cap and nowhere to dead-letter. A task
that always fails its handler is redelivered forever, invisibly. That is worse
here than on the control plane: the executor *deliberately* skips the ack to
signal backpressure (see main.py), so "redelivered again" is its normal healthy
state and a genuinely poisonous task looks exactly like a busy one.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig

NS = "simplemdg/event/core-2"
TOPIC = "workflow.tasks"


def _client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="id",
        client_secret="secret",
        namespace=NS,
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


class TestTheTransportsGetSeparateQueues:
    def test_rest_keeps_the_live_queue_name_exactly(self):
        """`sub-workflow.tasks` exists and is being consumed right now.
        Renaming a live queue is the migration that caused three outages."""
        assert EventMeshBrokerClient.queue_local_from_topic(TOPIC) == "sub-workflow.tasks"
        assert (
            EventMeshBrokerClient.queue_local_from_topic(TOPIC, transport="rest")
            == "sub-workflow.tasks"
        )

    def test_amqp_gets_its_own_queue(self):
        assert (
            EventMeshBrokerClient.queue_local_from_topic(TOPIC, transport="amqp")
            != EventMeshBrokerClient.queue_local_from_topic(TOPIC, transport="rest")
        )

    def test_the_amqp_name_follows_the_control_planes_convention(self):
        """It is a new queue, so it takes the dot-free form its five siblings
        already use (sub-workflow-results-amqp, sub-realtime-events-amqp, …)."""
        assert (
            EventMeshBrokerClient.queue_local_from_topic(TOPIC, transport="amqp")
            == "sub-workflow-tasks-amqp"
        )

    def test_an_empty_transport_is_rest(self):
        assert EventMeshBrokerClient.queue_local_from_topic(TOPIC, transport="") == \
            "sub-workflow.tasks"


class TestSubscribeUsesTheRightQueue:
    def test_amqp_subscribes_to_the_amqp_queue(self):
        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c._provision_subscription = MagicMock()
        c._start_amqp_consumer = MagicMock(return_value=True)

        c.subscribe_to_topic(TOPIC, lambda _d: None)

        c._start_amqp_consumer.assert_called_once_with(f"{NS}/sub-workflow-tasks-amqp")
        assert list(c._callbacks) == [f"{NS}/sub-workflow-tasks-amqp"]

    def test_rest_subscribes_to_the_historical_queue(self):
        c = _client(consume_transport="rest")
        c._provision_subscription = MagicMock()
        c._start_consumer_pool = MagicMock()

        c.subscribe_to_topic(TOPIC, lambda _d: None)

        assert list(c._callbacks) == [f"{NS}/sub-workflow.tasks"]

    def test_a_refused_amqp_attach_falls_back_without_leaking_a_handler(self):
        """Otherwise the AMQP queue keeps a registered callback nothing feeds,
        and `sample`-style loops would treat it as a live subscription."""
        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c._provision_subscription = MagicMock()
        c._start_amqp_consumer = MagicMock(return_value=False)
        c._start_consumer_pool = MagicMock()

        c.subscribe_to_topic(TOPIC, lambda _d: None)

        assert list(c._callbacks) == [f"{NS}/sub-workflow.tasks"]
        c._start_consumer_pool.assert_called_once()

    def test_the_handler_is_registered_before_the_link_can_deliver(self):
        """`_start_amqp_consumer` probes AND attaches with real credit, so the
        broker may deliver during the call. A delivery landing on an empty
        handler list used to be acked — i.e. destroyed."""
        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c._provision_subscription = MagicMock()
        seen: list[list[str]] = []

        def _attach(queue_name):
            seen.append(list(c._callbacks.get(queue_name, [])))
            return True

        c._start_amqp_consumer = _attach
        cb = lambda _d: None
        c.subscribe_to_topic(TOPIC, cb)

        assert seen == [[cb]], "callback must already be registered at attach time"


class TestTheG1Guard:
    def test_it_refuses_a_queue_that_already_has_consumers(self):
        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c.consumer_count = MagicMock(return_value=3)

        assert c._queue_is_free(f"{NS}/sub-workflow-tasks-amqp") is False

    def test_it_allows_a_queue_nothing_is_bound_to(self):
        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c.consumer_count = MagicMock(return_value=0)

        assert c._queue_is_free(f"{NS}/sub-workflow-tasks-amqp") is True

    def test_an_unreadable_count_does_not_stop_consumption(self):
        """Refuse only on positive knowledge — a management-API blip must not
        be the thing that halts task intake."""
        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c.consumer_count = MagicMock(return_value=None)

        assert c._queue_is_free(f"{NS}/sub-workflow-tasks-amqp") is True

    def test_it_runs_before_the_probe_not_just_before_add_queue(self, monkeypatch):
        """The probe attaches a real receiver link and grants credit, so a busy
        queue can strand messages during the probe itself. Refusing must happen
        before the consumer is even constructed."""
        from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amod

        monkeypatch.setattr(amod, "amqp_available", lambda: True)
        built = MagicMock()
        monkeypatch.setattr(amod, "EventMeshAMQPConsumer", built)

        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c.consumer_count = MagicMock(return_value=3)
        c._amqp_state = None

        assert c._start_amqp_consumer(f"{NS}/sub-workflow-tasks-amqp") is False
        built.assert_not_called()
        # Refusing this queue must not commit the whole process to REST — a
        # different queue may still be free.
        assert c._amqp_state != "rest"

    def test_a_free_queue_still_reaches_the_probe(self, monkeypatch):
        """Guard the guard: the test above must fail for the G1 reason, not
        because nothing ever gets that far in this environment."""
        from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as amod

        monkeypatch.setattr(amod, "amqp_available", lambda: True)
        built = MagicMock()
        built.return_value.probe.return_value = True
        monkeypatch.setattr(amod, "EventMeshAMQPConsumer", built)

        c = _client(consume_transport="amqp", amqp_url="wss://em.example.com/x")
        c.consumer_count = MagicMock(return_value=0)
        c._amqp_state = None

        assert c._start_amqp_consumer(f"{NS}/sub-workflow-tasks-amqp") is True
        built.return_value.probe.assert_called_once()


class TestQueueProvisioning:
    def test_a_queue_is_created_with_a_redelivery_cap_and_a_dmq(self):
        c = _client()
        body = c._queue_body(f"{NS}/sub-workflow-tasks-amqp")

        # The cap's VALUE is load-bearing and argued in TestBackpressureIsNotFailure.
        assert body["maxRedeliveryCount"] > 0
        assert body["deadMsgQueue"] == f"{NS}/sub-workflow-tasks-amqp-dmq"

    def test_a_dmq_gets_neither_a_cap_nor_a_dmq_of_its_own(self):
        """Otherwise a message failing in the DMQ needs somewhere else to go."""
        c = _client()
        body = c._queue_body(f"{NS}/sub-workflow-tasks-amqp-dmq")

        assert "maxRedeliveryCount" not in body
        assert "deadMsgQueue" not in body

    def test_is_dmq_is_not_expressible_as_dmq_name_equality(self):
        """`dmq_name` APPENDS the suffix, so `dmq_name(q) == q` never holds —
        a guard written that way would poll a `-dmq-dmq` that does not exist."""
        c = _client()
        q = f"{NS}/sub-workflow-tasks-amqp"

        assert c.dmq_name(q) != q
        assert c.is_dmq(c.dmq_name(q)) is True
        assert c.is_dmq(q) is False

    def test_the_dmq_exists_before_it_is_referenced(self):
        """A dangling deadMsgQueue reference dead-letters into a 404."""
        c = _client()
        c.queue_exists = MagicMock(return_value=False)
        c.create_queue = MagicMock()

        c.ensure_queue(f"{NS}/sub-workflow-tasks-amqp")

        created = [call.args[0] for call in c.create_queue.call_args_list]
        assert created.index(f"{NS}/sub-workflow-tasks-amqp-dmq") < created.index(
            f"{NS}/sub-workflow-tasks-amqp"
        )

    def test_an_unprovisionable_dmq_drops_the_cap_rather_than_dangle(self):
        """Unbounded redelivery is bad; dead-lettering into the void is worse."""
        c = _client()
        c.queue_exists = MagicMock(return_value=False)

        def _create(name, **kwargs):
            if name.endswith("-dmq"):
                raise RuntimeError("quota exceeded")

        c.create_queue = MagicMock(side_effect=_create)
        c.ensure_queue(f"{NS}/sub-workflow-tasks-amqp")

        final = c.create_queue.call_args_list[-1]
        assert final.args[0] == f"{NS}/sub-workflow-tasks-amqp"
        assert final.kwargs["with_dmq"] is False

    def test_an_existing_queue_is_converged_not_recreated(self):
        """The PUT is an upsert. Delete + recreate drops every message
        published while no queue is subscribed — and the publisher still gets
        a 204, so the loss is silent."""
        c = _client()
        c.queue_exists = MagicMock(return_value=True)
        c.create_queue = MagicMock()
        c.delete_queue = MagicMock()

        c.ensure_queue(f"{NS}/sub-workflow.tasks")

        c.delete_queue.assert_not_called()
        c.create_queue.assert_called_once()

    def test_a_policy_update_that_fails_on_an_existing_queue_is_not_fatal(self):
        """The queue is already usable; only the convergence missed. Startup
        must not die on a management-API blip that never used to be on this
        path at all."""
        c = _client()
        c.queue_exists = MagicMock(return_value=True)
        c.create_queue = MagicMock(side_effect=RuntimeError("management down"))

        c.ensure_queue(f"{NS}/sub-workflow.tasks")  # does not raise

    def test_a_first_creation_that_fails_is_fatal(self):
        """There is nothing to consume from — failing loudly beats a service
        that starts and silently never receives a task."""
        c = _client()
        c.queue_exists = MagicMock(return_value=False)
        c._ensure_dmq = MagicMock(return_value=True)
        c.create_queue = MagicMock(side_effect=RuntimeError("nope"))

        with pytest.raises(RuntimeError):
            c.ensure_queue(f"{NS}/sub-workflow-tasks-amqp")


class TestAnUnknownQueueIsNotDataLoss:
    def test_no_handler_leaves_the_message_unsettled(self):
        """Returning True here acked the message, destroying it. Redelivery is
        the right answer to both ways of getting here: a startup race, or a key
        mismatch — the second exhausts maxRedeliveryCount into the DMQ, which
        is alertable and recoverable."""
        c = _client()

        assert c._dispatch_to_callbacks(f"{NS}/sub-nobody-listens", {"x": 1}) is False

    def test_the_rest_path_then_skips_the_ack(self):
        c = _client()
        c.acknowledge = MagicMock()

        c._dispatch_message(
            f"{NS}/sub-nobody-listens", {"data": {"x": 1}, "message_id": "m-1"},
        )

        c.acknowledge.assert_not_called()


class TestBackpressureIsNotFailure:
    """The executor signals "I am full" by NOT acking, so the broker re-offers
    the task once a slot frees (main.py raises on CONCURRENCY_LIMIT /
    WORKER_SATURATED / ADMISSION_FULL). Every knob that treats a redelivery as
    a failure has to account for that, or normal load becomes data loss."""

    def test_prefetch_does_not_hoard_a_backlog_behind_a_saturated_instance(self):
        """Every prefetched message is one the broker already considers
        delivered and will not offer to an instance that does have capacity."""
        c = _client()
        assert c._amqp_prefetch == 10

    def test_the_prefetch_default_survives_the_bootstrap_path(self):
        """app_config supplies an explicit value, so a default set only on the
        dataclass would be overridden before it was ever read."""
        from app.layer4_frameworks.config.app_config import settings

        assert settings.EVENT_MESH_AMQP_PREFETCH == 10

    def test_the_redelivery_cap_leaves_room_for_sustained_saturation(self):
        """At a 60s lock TTL a cap of N is roughly N minutes of saturation
        before a HEALTHY task is dead-lettered. The control plane's 5 would be
        five minutes — well inside a normal batch on this service — so it would
        silently convert correct backpressure into lost work."""
        c = _client()

        assert c._max_redelivery_count >= 50
        minutes = c._max_redelivery_count * (c._lock_duration_ms / 1000) / 60
        assert minutes >= 30

    def test_the_cap_default_survives_the_bootstrap_path(self):
        from app.layer4_frameworks.config.app_config import settings

        assert settings.BROKER_MAX_REDELIVERY_COUNT >= 50

    def test_but_it_is_still_capped_so_a_poison_task_stops_cycling(self):
        """maxRedeliveryCount=0 — what the live queue has today — means a task
        that always fails is redelivered forever, invisibly."""
        c = _client()
        body = c._queue_body(f"{NS}/sub-workflow.tasks")

        assert body["maxRedeliveryCount"] > 0
        assert body["deadMsgQueue"]
