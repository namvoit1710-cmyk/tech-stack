"""What EVENT_MESH_RUNTIME_FALLBACK=1 brings back — the warning label.

This file used to prove a live defect. The defect is fixed (see
``test_event_mesh_no_runtime_fallback``); the mechanism survives behind the
escape hatch, so these tests now run WITH the hatch on and exist to make
anyone who reaches for it read what it costs first.

Scaling the control plane to 2 instances re-creates a retired subscription.

The chain, end to end, with nothing above the HTTP layer mocked out:

1. Instance 0 holds the only AMQP link, so the broker reports
   ``consumerCount = 1`` on ``sub-orchestration-actions-amqp``.
2. Instance 1 boots. G1 (``_queue_is_free``) refuses to attach beside another
   consumer and falls back to REST on the BARE queue name.
3. The fallback calls the real ``provision_subscription``, which finds no
   subscription (P5.4 deleted it) and **creates one**.
4. The topic now fans out to two queues, so every orchestration action is
   delivered twice — once to instance 0 over AMQP, once to instance 1 over
   REST. That is not load sharing, it is duplication.

``test_event_mesh_attach_guard`` already pins step 2 (the fallback provisions
the bare queue). What was never pinned is step 3 — that provisioning a queue
whose subscription was deliberately removed puts it back. That is the step
that turns a degraded second pod into a duplicating one, and it is asserted
here against the real ``EventMeshAdmin``.

The control test is the point of the pair: with ``consumerCount = 0`` the same
code touches the bare queue not at all. Nothing here is about REST being slow;
it is about a recovery path re-arming the fanout that the AMQP cutover removed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import httpx
import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig

NAMESPACE = "simplemdg/event/core-2"
TOPIC = "orchestration.actions"
BARE_LOCAL = "sub-orchestration-actions"
AMQP_LOCAL = f"{BARE_LOCAL}-amqp"
BARE_Q = f"{NAMESPACE}/{BARE_LOCAL}"
AMQP_Q = f"{NAMESPACE}/{AMQP_LOCAL}"

# The '/' immediately after the queue name is what tells the bare queue apart
# from the AMQP one — ``sub-orchestration-actions`` is a PREFIX of
# ``sub-orchestration-actions-amqp``, so a substring check without the
# delimiter passes on the wrong queue and proves nothing.
BARE_SUBSCRIPTION = f"{BARE_LOCAL}/subscriptions/"
AMQP_SUBSCRIPTION = f"{AMQP_LOCAL}/subscriptions/"


class _Resp:
    """Minimal httpx.Response stand-in for the management API."""

    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=MagicMock(), response=self,
            )


def _make_client() -> EventMeshBrokerClient:
    return EventMeshBrokerClient(EventMeshConfig(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace=NAMESPACE,
        consume_transport="amqp",
        amqp_url="wss://em.example.com/protocols/amqp10ws",
        # The hatch. Without it every assertion below inverts.
        runtime_fallback=True,
    ))


def _broker_state(*, amqp_queue_consumers: int):
    """Route management GETs the way the live broker would after P5.4.

    * every queue exists (200)
    * the AMQP queue reports ``amqp_queue_consumers`` bound consumers
    * NO subscription exists anywhere — 404 — which is the post-P5.4 state
      for the bare queue and the thing the fallback is about to change
    """
    def _get(url: str):
        plain = unquote(url)
        if "/subscriptions/" in plain:
            return _Resp(404)
        if plain.endswith(AMQP_LOCAL):
            return _Resp(200, {"consumerCount": amqp_queue_consumers})
        return _Resp(200, {"consumerCount": 0})
    return _get


@pytest.fixture
def wired():
    """A client whose management API is stubbed and whose PUTs are recorded."""
    client = _make_client()
    puts: list[str] = []
    http = MagicMock()
    http.put.side_effect = lambda url, **_kw: (puts.append(unquote(url)), _Resp(200))[1]
    client._admin._clients.sync = http
    with patch.object(client._admin._tokens, "auth_headers", return_value={}):
        yield client, puts
    client.close()


def _amqp_is_healthy():
    """Make every AMQP failure mode EXCEPT G1 impossible.

    Without this the test environment supplies its own fallback: ``proton`` is
    not installed here, so ``_start_amqp_consumer`` returns False at
    ``amqp_available()`` and never reaches ``_queue_is_free``. The first draft
    of this file passed exactly that way — it proved "some AMQP failure
    re-subscribes", which is true but is not the scale-out claim.

    With the libs reported present and a probe that succeeds, the ONLY thing
    left that can produce a fallback is the peer-consumer refusal. Each test
    also asserts the consumer class was never constructed, which pins that G1
    ran before the probe rather than after it.
    """
    fake = MagicMock()
    fake.probe.return_value = True
    return patch(
        "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.amqp_available",
        return_value=True,
    ), patch(
        "app.layer4_frameworks.messaging.event_mesh_amqp_consumer"
        ".EventMeshAMQPConsumer",
        return_value=fake,
    )


class TestSecondInstanceResubscribesTheRetiredQueue:
    def test_it_creates_a_subscription_on_the_bare_queue(self, wired):
        """The defect. One peer consumer is enough to put the fanout back."""
        client, puts = wired
        avail, cls = _amqp_is_healthy()
        with patch.object(
            client._admin, "_management_get",
            side_effect=_broker_state(amqp_queue_consumers=1),
        ), patch.object(client._consumer, "_poll_loop"), avail, cls as amqp_cls:
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        amqp_cls.assert_not_called()  # refused by G1, not by a broken probe
        created = [u for u in puts if BARE_SUBSCRIPTION in u]
        assert created, (
            "instance 1 fell back to REST and did NOT re-create the "
            "subscription — the chain is broken somewhere before step 3.\n"
            f"PUTs issued: {puts}"
        )

    def test_it_also_starts_consuming_from_it(self, wired):
        """A re-created subscription alone would only fill a queue. The pod
        polls it too, so the duplicate is actually PROCESSED — and it does
        this at boot, before any readiness gate could have an opinion."""
        client, puts = wired
        avail, cls = _amqp_is_healthy()
        with patch.object(
            client._admin, "_management_get",
            side_effect=_broker_state(amqp_queue_consumers=1),
        ), patch.object(client._consumer, "_poll_loop"), avail, cls as amqp_cls:
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        amqp_cls.assert_not_called()
        assert BARE_Q in client._consumer._consumer_threads
        # NOT "rest". ``_start_amqp_consumer`` commits the process to REST only
        # for proton_missing / amqp_unreachable; a G1 refusal returns early and
        # leaves the state undecided. See the per-topic test below for why that
        # distinction decides the blast radius.
        assert client._consumer._amqp_state is None

    def test_the_amqp_queue_is_provisioned_but_never_polled(self, wired):
        """Provisioning the AMQP queue + its subscription happens BEFORE the
        guard runs, so those PUTs are expected and correct. What must not
        happen is REST-polling it — that would create the 3 consumer flows
        that strand the next attach.

        The first draft asserted no AMQP subscription PUT at all and failed.
        The code was right; the expectation was wrong."""
        client, puts = wired
        avail, cls = _amqp_is_healthy()
        with patch.object(
            client._admin, "_management_get",
            side_effect=_broker_state(amqp_queue_consumers=1),
        ), patch.object(client._consumer, "_poll_loop"), avail, cls:
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert [u for u in puts if AMQP_SUBSCRIPTION in u], "AMQP queue not provisioned"
        assert AMQP_Q not in client._consumer._consumer_threads


class TestTheRefusalIsPerQueueSoEveryWorkTopicDuplicates:
    """G1's refusal is not sticky, and that is what sets the blast radius.

    ``proton_missing`` and ``amqp_unreachable`` set ``_amqp_state = "rest"``,
    which short-circuits every later subscribe — one decision for the process.
    A peer-consumer refusal returns before that assignment, so instance 1
    re-tries AMQP for the NEXT topic, is refused again, and falls back again.

    Consequence: at 2 instances it is not one duplicated topic. Every work
    topic the control plane consumes gets its retired queue re-subscribed,
    independently."""

    def test_two_topics_both_get_their_bare_queue_resubscribed(self, wired):
        client, puts = wired
        second_topic = "workflow.results"
        second_bare = "sub-workflow-results/subscriptions/"

        def _state(url: str):
            plain = unquote(url)
            if "/subscriptions/" in plain:
                return _Resp(404)
            # every AMQP-named queue already has instance 0's link on it
            if plain.endswith("-amqp"):
                return _Resp(200, {"consumerCount": 1})
            return _Resp(200, {"consumerCount": 0})

        avail, cls = _amqp_is_healthy()
        with patch.object(client._admin, "_management_get", side_effect=_state), \
             patch.object(client._consumer, "_poll_loop"), avail, cls:
            client.subscribe_to_topic(TOPIC, lambda _m: None)
            client.subscribe_to_topic(second_topic, lambda _m: None)

        assert [u for u in puts if BARE_SUBSCRIPTION in u], "actions not re-subscribed"
        assert [u for u in puts if second_bare in u], (
            "the second topic did NOT re-subscribe — the refusal would have to "
            "be sticky for that, and it is not.\n"
            f"PUTs issued: {puts}"
        )


class TestTheFallbackIsWiderThanScaleOut:
    def test_any_amqp_failure_re_subscribes_the_retired_queue(self, wired):
        """Scaling to 2 is only the most predictable trigger. The same
        recovery path runs when the AMQP libs are missing from the image or
        the endpoint is unreachable — a single pod that loses AMQP for any
        reason also puts the fanout back. Here ``proton`` is genuinely absent
        (nothing patches it), which is how the first draft of this file
        accidentally passed."""
        client, puts = wired
        with patch.object(
            client._admin, "_management_get",
            side_effect=_broker_state(amqp_queue_consumers=0),
        ), patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert client._consumer._amqp_state == "rest"
        assert [u for u in puts if BARE_SUBSCRIPTION in u]


class TestAFreeQueueNeverTouchesTheBareOne:
    """Control. Same code, same stubs, one different number — and the bare
    queue is neither provisioned nor subscribed nor polled. This is what
    isolates the cause to G1's refusal rather than to startup in general."""

    def test_no_subscription_is_created_on_the_bare_queue(self, wired):
        client, puts = wired
        fake_amqp = MagicMock()
        fake_amqp.probe.return_value = True
        with patch.object(
            client._admin, "_management_get",
            side_effect=_broker_state(amqp_queue_consumers=0),
        ), patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.amqp_available",
            return_value=True,
        ), patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer"
            ".EventMeshAMQPConsumer",
            return_value=fake_amqp,
        ):
            client.subscribe_to_topic(TOPIC, lambda _m: None)

        assert not [u for u in puts if BARE_SUBSCRIPTION in u], (
            f"the bare queue was subscribed on the healthy path too: {puts}"
        )
        assert client._consumer._amqp_state == "amqp"
        assert BARE_Q not in client._consumer._consumer_threads
