"""Step A — the consume transport is a CONFIGURATION decision, never a runtime one.

Reproduced on tenant-1 2026-08-11: a second instance boots, G1 refuses to
attach beside the peer's AMQP link, and the REST fallback re-creates the
subscription P5.4 deleted — on FIVE topics, each leaving 3 broker-side consumer
flows that outlive the pod. Every message on those topics is then delivered
twice.

The bug is not that a fallback exists. It is that three unrelated failures
collapse into one recovery:

    peer_owns_queue    another pod already consumes this queue. Nothing is
                       lost by standing down, and standing down is correct.
    amqp_unreachable   possibly transient. Stand down, keep retrying.
    proton_missing     an image defect. Stand down, do not retry, be loud.

None of the three justifies provisioning a retired queue. This file pins the
new behaviour: stand down, never improvise, and come back when the condition
clears. ``EVENT_MESH_RUNTIME_FALLBACK=1`` restores the old path for an
emergency, and that escape hatch is tested rather than assumed.

The regression that matters most is the LAST class: an explicitly configured
``consume_transport="rest"`` must still provision and poll the bare queue.
REST is the documented rollback target — what this change removes is the
runtime switch, not the transport.
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
from app.layer4_frameworks.messaging import event_mesh_consumer as emc

NAMESPACE = "simplemdg/event/core-2"
TOPIC = "orchestration.actions"
BARE_LOCAL = "sub-orchestration-actions"
AMQP_LOCAL = f"{BARE_LOCAL}-amqp"
BARE_Q = f"{NAMESPACE}/{BARE_LOCAL}"
AMQP_Q = f"{NAMESPACE}/{AMQP_LOCAL}"

# The trailing '/' is load-bearing: ``sub-orchestration-actions`` is a PREFIX of
# ``sub-orchestration-actions-amqp``, so a bare substring check passes on the
# wrong queue and proves nothing.
BARE_SUBSCRIPTION = f"{BARE_LOCAL}/subscriptions/"


class _Resp:
    def __init__(self, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = ""

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                str(self.status_code), request=MagicMock(), response=self,
            )


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


def _broker(*, amqp_consumers: int):
    """Post-P5.4 broker: queues exist, NO subscription anywhere, and EVERY
    ``-amqp`` queue carries ``amqp_consumers`` bound consumers.

    Every AMQP queue, not just one: a peer instance holds a link on all of
    them, which is exactly what made the live blast radius five topics. An
    earlier version of this helper keyed on a single queue name, so the
    per-topic test attached cleanly on the second topic and reported a bug
    in the code that was really a hole in the stub.
    """
    def _get(url: str):
        plain = unquote(url)
        if "/subscriptions/" in plain:
            return _Resp(404)
        if plain.endswith("-amqp"):
            return _Resp(200, {"consumerCount": amqp_consumers})
        return _Resp(200, {"consumerCount": 0})
    return _get


def _amqp_libs_present(probe_ok: bool = True):
    """Report proton importable and make ``probe()`` return *probe_ok*.

    Without this the test environment supplies its own fallback — ``proton`` is
    genuinely absent here — and a test meant to exercise G1 exercises
    ``amqp_available()`` instead. That mistake produced a green suite that
    proved nothing, so every test below states which cause it is driving.
    """
    fake = MagicMock()
    fake.probe.return_value = probe_ok
    return (
        patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer.amqp_available",
            return_value=True,
        ),
        patch(
            "app.layer4_frameworks.messaging.event_mesh_amqp_consumer"
            ".EventMeshAMQPConsumer",
            return_value=fake,
        ),
        fake,
    )


@pytest.fixture
def wired():
    """Client with a stubbed management API and every PUT recorded."""
    made: list[EventMeshBrokerClient] = []
    puts: list[str] = []

    def build(**overrides):
        client = _make_client(**overrides)
        http = MagicMock()
        http.put.side_effect = lambda url, **_kw: (
            puts.append(unquote(url)), _Resp(200),
        )[1]
        client._admin._clients.sync = http
        made.append(client)
        return client, puts

    with patch(
        "app.layer4_frameworks.messaging.event_mesh_token_provider"
        ".EventMeshTokenProvider.auth_headers",
        return_value={},
    ):
        yield build

    for client in made:
        client.close()


def _subscribe(client, broker_state, topic=TOPIC):
    with patch.object(client._admin, "_management_get", side_effect=broker_state), \
         patch.object(client._consumer, "_poll_loop"):
        client.subscribe_to_topic(topic, lambda _m: None)


class TestStandDownInsteadOfImprovising:
    """No cause justifies provisioning a retired queue."""

    def test_a_peer_consumer_stands_the_topic_down(self, wired):
        client, puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with avail, cls as amqp_cls:
            _subscribe(client, _broker(amqp_consumers=1))
        amqp_cls.assert_not_called()  # refused by G1, not by a broken probe

        assert not [u for u in puts if BARE_SUBSCRIPTION in u], (
            f"the retired queue was subscribed again: {puts}"
        )
        assert BARE_Q not in client._consumer._consumer_threads
        assert BARE_Q not in client._consumer._callbacks
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_PEER}

    def test_missing_proton_stands_the_topic_down(self, wired):
        """Wider than scale-out: one pod losing AMQP took the same path."""
        client, puts = wired()
        _subscribe(client, _broker(amqp_consumers=0))  # proton genuinely absent

        assert not [u for u in puts if BARE_SUBSCRIPTION in u]
        assert BARE_Q not in client._consumer._consumer_threads
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_NO_LIBS}

    def test_an_unreachable_endpoint_stands_the_topic_down(self, wired):
        client, puts = wired()
        avail, cls, _ = _amqp_libs_present(probe_ok=False)
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=0))

        assert not [u for u in puts if BARE_SUBSCRIPTION in u]
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_UNREACHABLE}

    def test_every_topic_stands_down_not_just_the_first(self, wired):
        """The G1 refusal is not sticky, which is why the live blast radius was
        five topics rather than one. Standing down must be per-topic too."""
        client, puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=1), TOPIC)
            _subscribe(client, _broker(amqp_consumers=1), "workflow.results")

        assert not [u for u in puts if "/subscriptions/" in u and "-amqp" not in u]
        assert set(client._consumer.stood_down()) == {TOPIC, "workflow.results"}


class TestTheStandDownIsVisible:
    def test_it_is_reported_on_the_transport_snapshot(self, wired):
        client, puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))

        state = client._consumer.transport_state()
        assert state["stood_down"] == {TOPIC: emc.ATTACH_PEER}
        assert state["rest_fallback"] is False, (
            "standing down is not a REST fallback — reporting it as one would "
            "re-point the runbook at the wrong procedure"
        )

    def test_the_metric_keeps_a_stable_label_set(self, wired):
        """A Prometheus label set is frozen on first use — a later sample with
        different keys is dropped silently."""
        client, puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with patch.object(
            client._consumer._metrics, "record_consume_standdown",
        ) as rec, avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))

        assert rec.call_count == 1
        assert rec.call_args.kwargs or len(rec.call_args.args) == 2
        queue, reason = rec.call_args.args
        assert queue == AMQP_Q and reason == emc.ATTACH_PEER


class TestRetryBringsTheTopicBack:
    """Standing down without a way back would turn a transient AMQP blip into
    a pod that consumes nothing until someone restarts it."""

    def test_a_peer_standdown_recovers_when_the_peer_goes_away(self, wired):
        client, puts = wired()
        avail, cls, fake = _amqp_libs_present()
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))
            assert client._consumer.stood_down()

            # The peer disappears (scale-down, crash) — the queue is free now.
            with patch.object(
                client._admin, "_management_get", side_effect=_broker(amqp_consumers=0),
            ):
                recovered = client._consumer.retry_stood_down()

        assert recovered == [TOPIC]
        assert client._consumer.stood_down() == {}
        assert AMQP_Q in client._consumer._callbacks, "callback not re-registered"
        assert not [u for u in puts if BARE_SUBSCRIPTION in u], (
            "recovery must not touch the retired queue either"
        )

    def test_a_peer_standdown_stays_down_while_the_peer_is_there(self, wired):
        client, puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))
            with patch.object(
                client._admin, "_management_get", side_effect=_broker(amqp_consumers=1),
            ):
                assert client._consumer.retry_stood_down() == []
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_PEER}

    def test_missing_proton_is_never_retried(self, wired):
        """An import that failed does not start working. Retrying it forever
        would be a busy loop that hides an image defect."""
        client, puts = wired()
        _subscribe(client, _broker(amqp_consumers=0))
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_NO_LIBS}

        with patch.object(
            client._admin, "_management_get", side_effect=_broker(amqp_consumers=0),
        ) as mgmt:
            assert client._consumer.retry_stood_down() == []
        mgmt.assert_not_called()  # not even a management call is worth making
        assert client._consumer.stood_down() == {TOPIC: emc.ATTACH_NO_LIBS}


class TestTheRefusalDoesNotSpamTheLog:
    """Measured on tenant-1 2026-08-11 after the fix went live: the retry loop
    re-logged five ERROR lines every 30 seconds — 10 a minute, forever, for a
    condition that is HEALTHY at two instances. Teaching an operator to ignore
    ERROR is its own outage."""

    def test_the_first_refusal_is_visible_and_repeats_are_not(self, wired, caplog):
        client, _puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with caplog.at_level("INFO"), avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))
            with patch.object(
                client._admin, "_management_get", side_effect=_broker(amqp_consumers=1),
            ):
                client._consumer.retry_stood_down()
                client._consumer.retry_stood_down()

        loud = [r for r in caplog.records
                if "Not attaching AMQP" in r.message and r.levelname != "DEBUG"]
        assert len(loud) == 1, f"refusal logged loudly {len(loud)} times"

    def test_it_never_claims_to_be_rest_polling(self, wired, caplog):
        """The old text said 'Consuming via REST instead.' — true before the
        fix, a lie after it, and the first thing an operator reads."""
        client, _puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with caplog.at_level("DEBUG"), avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))
        assert not [r for r in caplog.records if "via REST instead" in r.message]

    def test_a_freed_queue_makes_the_next_refusal_loud_again(self, wired, caplog):
        client, _puts = wired()
        avail, cls, _ = _amqp_libs_present()
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))
            with patch.object(
                client._admin, "_management_get", side_effect=_broker(amqp_consumers=0),
            ):
                client._consumer.retry_stood_down()  # recovers, clears the flag
            assert client._consumer.stood_down() == {}
            with caplog.at_level("INFO"), patch.object(
                client._admin, "_management_get", side_effect=_broker(amqp_consumers=1),
            ):
                client._consumer._queue_is_free(AMQP_Q)

        loud = [r for r in caplog.records
                if "Not attaching AMQP" in r.message and r.levelname != "DEBUG"]
        assert len(loud) == 1, "a fresh refusal after recovery must be loud"


class TestTheEscapeHatch:
    def test_runtime_fallback_on_restores_the_old_behaviour(self, wired):
        """One env var, reversible with cf set-env + restart, no redeploy."""
        client, puts = wired(runtime_fallback=True)
        avail, cls, _ = _amqp_libs_present()
        with avail, cls:
            _subscribe(client, _broker(amqp_consumers=1))

        assert [u for u in puts if BARE_SUBSCRIPTION in u], (
            "the escape hatch did not restore the fallback"
        )
        assert BARE_Q in client._consumer._consumer_threads
        assert client._consumer.stood_down() == {}

    def test_it_is_off_by_default(self):
        assert EventMeshConfig(
            token_url="https://a", messaging_url="https://b",
            management_url="https://c", client_id="i", client_secret="s",
        ).runtime_fallback is False


class TestConfiguredRestIsUntouched:
    """The rollback target. What this change removes is the runtime SWITCH —
    REST as a chosen transport must behave exactly as before."""

    def test_it_provisions_and_polls_the_bare_queue(self, wired):
        client, puts = wired(consume_transport="rest")
        _subscribe(client, _broker(amqp_consumers=0))

        assert [u for u in puts if BARE_SUBSCRIPTION in u]
        assert BARE_Q in client._consumer._consumer_threads
        assert BARE_Q in client._consumer._callbacks
        assert client._consumer.stood_down() == {}

    def test_it_never_touches_the_amqp_queue(self, wired):
        client, puts = wired(consume_transport="rest")
        _subscribe(client, _broker(amqp_consumers=0))
        assert AMQP_Q not in client._consumer._consumer_threads
