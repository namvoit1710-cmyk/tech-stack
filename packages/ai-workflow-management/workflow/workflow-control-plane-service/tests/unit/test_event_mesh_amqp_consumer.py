"""Tests for the AMQP push consumer + broker consume-transport selection.

These run WITHOUT ``python-qpid-proton`` installed, so they cover the parts
knowable offline: transport selection, the automatic fall-back to REST polling,
the queue-address builder, and body parsing. The proton wire path itself needs
a live Event Mesh endpoint and is validated in BTP.
"""

from __future__ import annotations

from types import SimpleNamespace
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
    monitor = overrides.pop("monitor", None)
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace="simplemdg/event/core-2",
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults), monitor=monitor)


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
        assert client._consumer._consume_transport == "rest"

    def test_amqp_url_parsed_into_host_port_path(self):
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com:443/protocols/amqp10ws",
        )
        assert client._consumer._consume_transport == "amqp"
        assert client._consumer._amqp_host == "em.example.com"
        assert client._consumer._amqp_port == 443
        assert client._consumer._amqp_ws_path == "/protocols/amqp10ws"

    def test_explicit_rest_never_builds_amqp(self):
        client = _make_client(
            consume_transport="rest",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
        )
        with patch.object(client._admin, "queue_exists", return_value=True), \
             patch.object(client._admin, "subscription_exists", return_value=True), \
             patch.object(client._consumer, "_poll_loop"):
            client.subscribe_to_topic("run.status", lambda m: None)
        assert client._consumer._amqp_state is None
        assert client._consumer._amqp_consumer is None
        client.close()


class TestAmqpAutoFallbackToRest:
    """The auto-fallback is now OPT-IN (``EVENT_MESH_RUNTIME_FALLBACK``).

    It used to be automatic, and that is what re-created the retired REST
    subscriptions on tenant-1 2026-08-11 — five topics, every message
    duplicated, from a 65-second two-instance window. The mechanism still
    works and is still worth pinning; what changed is that a deployment has to
    ask for it. The default-off behaviour lives in
    ``test_event_mesh_no_runtime_fallback.py``.
    """

    def test_falls_back_to_rest_when_amqp_libs_missing(self):
        # When the AMQP libs aren't importable, the broker must fall back to the
        # REST poll loop instead of leaving consumption dead. Patch
        # amqp_available so this is deterministic whether or not proton/
        # websocket-client are installed in the test environment.
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
            runtime_fallback=True,
        )
        assert client._consumer._consume_transport == "amqp"  # host resolved from url
        with patch.object(client._admin, "queue_exists", return_value=True), \
             patch.object(client._admin, "subscription_exists", return_value=True), \
             patch.object(client._consumer, "_poll_loop"), \
             patch.object(amqp_mod, "amqp_available", return_value=False):
            client.subscribe_to_topic("run.status", lambda m: None)

        assert client._consumer._amqp_state == "rest"
        assert client._consumer._amqp_consumer is None
        assert client._consumer._consumer_threads  # REST fallback started
        client.close()

    def test_probe_failure_falls_back_and_sticks(self):
        """A failed connect probe commits the process to REST for all queues.

        Sticky only WITH the escape hatch on: without it there is nothing to
        commit to, and staying undecided is what lets a recovered endpoint be
        picked up by ``retry_stood_down``.
        """
        client = _make_client(
            consume_transport="amqp",
            amqp_url="wss://em.example.com/protocols/amqp10ws",
            runtime_fallback=True,
        )
        fake_consumer = MagicMock()
        fake_consumer.probe.return_value = False
        with patch.object(client._admin, "queue_exists", return_value=True), \
             patch.object(client._admin, "subscription_exists", return_value=True), \
             patch.object(client._consumer, "_poll_loop"), \
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

        assert client._consumer._amqp_state == "rest"
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
        with patch.object(client._admin, "queue_exists", return_value=True), \
             patch.object(client._admin, "subscription_exists", return_value=True), \
             patch.object(client._consumer, "_poll_loop") as poll, \
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

        assert client._consumer._amqp_state == "amqp"
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


# ── ghost-link teardown (2026-08-05) ──────────────────────────────────
#
# Closing the WebSocket under a live link leaves the broker holding a
# subscription with outstanding credit; it keeps routing messages to a
# consumer that will never settle them and they strand in the queue.
# Measured on a scratch queue: 3 of 20 messages lost this way.

class _FakeTransport:
    def __init__(self) -> None:
        self.pending_values = [0]
        self.sasl_obj = SimpleNamespace(outcome=0)

    def pending(self):
        return self.pending_values[0]

    def peek(self, _n):
        return b""

    def pop(self, _n):
        return None

    def sasl(self):
        return self.sasl_obj


class _Recorder:
    """Records close ordering across the link, connection and socket."""

    def __init__(self) -> None:
        self.order: list[str] = []

    def link(self):
        rec = self
        class _L:
            def close(self_inner):
                rec.order.append("link.close")
        return _L()

    def conn(self):
        rec = self
        class _C:
            def close(self_inner):
                rec.order.append("conn.close")
        return _C()

    def ws(self):
        rec = self
        class _W:
            def close(self_inner):
                rec.order.append("ws.close")
        return _W()


def _bridge_for_close(rec: _Recorder):
    from app.layer4_frameworks.messaging.event_mesh_amqp_consumer import _ProtonWsBridge
    b = _ProtonWsBridge(
        url="wss://h/p", address="queue:ns/q", queue_name="ns/q", token="t",
        prefetch=10, message_handler=lambda *_a: True, is_running=lambda: True,
        hostname="h",
    )
    b._receiver = rec.link()
    b._conn = rec.conn()
    b._ws = rec.ws()
    b._transport = _FakeTransport()
    return b


class TestGhostLinkTeardown:
    def test_close_detaches_link_and_connection_before_the_socket(self):
        """The broker must be told, not just disconnected from."""
        rec = _Recorder()
        _bridge_for_close(rec)._close()
        assert rec.order == ["link.close", "conn.close", "ws.close"]

    def test_close_still_closes_the_socket_when_the_link_raises(self):
        """A peer that already tore down must not wedge teardown."""
        rec = _Recorder()
        b = _bridge_for_close(rec)

        class _Boom:
            def close(self):
                raise RuntimeError("transport gone")

        b._receiver = _Boom()
        b._close()
        assert "ws.close" in rec.order, "the socket must close regardless"

    def test_close_is_bounded_when_frames_never_drain(self):
        """An unresponsive peer must not hang teardown forever."""
        import time as _t
        rec = _Recorder()
        b = _bridge_for_close(rec)
        b._transport.pending_values = [1]      # never drains
        b._ws = rec.ws()
        started = _t.monotonic()
        b._close()
        assert _t.monotonic() - started < 5.0, "teardown must be bounded"
        assert "ws.close" in rec.order

    def test_probe_grants_minimal_credit(self):
        """The probe must never become a consumer — it stops pumping at ready."""
        from app.layer4_frameworks.messaging import event_mesh_amqp_consumer as m
        captured = {}

        class _Bridge:
            def __init__(self, **kw):
                captured.update(kw)
                self.ready = True
                self.error = None
                self.events_seen = SimpleNamespace(total=0, counts=lambda: {})
                self.bytes_sent = self.bytes_recv = 0
                self.sasl_outcome = 0

            def run(self, deadline=None):
                return None

        c = _consumer(prefetch=50)
        with patch.object(m, "_ProtonWsBridge", _Bridge), \
             patch.object(m, "amqp_available", lambda: True):
            assert c.probe("ns/q") is True
        assert captured["prefetch"] == m._PROBE_PREFETCH
        assert captured["prefetch"] < 50, "probe must not hold the full prefetch"


# ── ready semantics (2026-08-05) ──────────────────────────────────────
#
# `ready` used to mean "the peer echoed an attach". AMQP 1.0 refuses a source
# by echoing the attach with a NULL source and then detaching, so a rejected
# link set it too. That made probe() report OK on a dead subscription, and
# made _receive_loop treat the session as healthy — observed: ~13 reconnects
# in 1.7s with no backoff at all.

_FAKE_EVENT = SimpleNamespace(
    LINK_REMOTE_OPEN="LINK_REMOTE_OPEN",
    LINK_REMOTE_CLOSE="LINK_REMOTE_CLOSE",
    LINK_REMOTE_DETACH="LINK_REMOTE_DETACH",
    CONNECTION_REMOTE_OPEN="CONNECTION_REMOTE_OPEN",
    CONNECTION_REMOTE_CLOSE="CONNECTION_REMOTE_CLOSE",
    SESSION_REMOTE_OPEN="SESSION_REMOTE_OPEN",
    TRANSPORT_ERROR="TRANSPORT_ERROR",
    TRANSPORT_CLOSED="TRANSPORT_CLOSED",
    DELIVERY="DELIVERY",
)


@pytest.fixture
def fake_event():
    """proton is not installed here, so ``amqp_mod.Event`` is None.

    Injecting sentinels lets the event-handling contract be tested offline —
    the whole point, given the wire path had no test that could fail.
    """
    with patch.object(amqp_mod, "Event", _FAKE_EVENT):
        yield _FAKE_EVENT


def _bridge(**kw):
    from app.layer4_frameworks.messaging.event_mesh_amqp_consumer import _ProtonWsBridge
    base = dict(url="wss://h/p", address="queue:ns/q", queue_name="ns/q", token="t",
                prefetch=10, message_handler=lambda *_a: True,
                is_running=lambda: True, hostname="h")
    base.update(kw)
    return _ProtonWsBridge(**base)


def _open_ev(receiver):
    return SimpleNamespace(type=_FAKE_EVENT.LINK_REMOTE_OPEN, receiver=receiver)


def _close_ev():
    return SimpleNamespace(type=_FAKE_EVENT.LINK_REMOTE_CLOSE, receiver=None,
                           link=None, connection=None, transport=None)


class _Receiver:
    """Minimal proton-receiver stand-in."""

    def __init__(self, address="queue:ns/q", source_missing=False):
        self.flowed = None
        self.remote_source = None if source_missing else SimpleNamespace(address=address)

    def flow(self, n):
        self.flowed = n


class TestReadySemantics:
    """`ready` must mean "attached AND confirmed", not "attach echoed".

    AMQP 1.0 refuses a source by echoing the attach with a NULL source and then
    detaching, so a rejected link used to set `ready` too. That made probe()
    report OK on a dead subscription and made _receive_loop treat the session
    as healthy — observed on 2026-08-05: ~13 reconnects in 1.7s, no backoff.
    """

    def test_attach_with_a_real_source_is_ready(self, fake_event):
        b = _bridge()
        rcv = _Receiver()
        b._handle_event(_open_ev(rcv))
        assert b.ready is True
        assert b.attached_at > 0
        assert rcv.flowed == 10, "credit is granted on a good attach"

    def test_attach_with_a_null_source_is_a_refusal(self, fake_event):
        b = _bridge()
        rcv = _Receiver(source_missing=True)
        b._handle_event(_open_ev(rcv))
        assert b.ready is False, "a null source must not count as attached"
        assert b._closed is True
        assert b.error is not None
        assert rcv.flowed is None, "no credit to a refused link"

    def test_attach_with_a_blank_address_is_a_refusal(self, fake_event):
        b = _bridge()
        rcv = _Receiver(address="")
        b._handle_event(_open_ev(rcv))
        assert b.ready is False
        assert b._closed is True

    def test_uninspectable_source_is_treated_as_ok(self, fake_event):
        """The guard must never turn a working link into a false negative."""
        class _Odd:
            flowed = None

            def flow(self, n):
                self.flowed = n

            @property
            def remote_source(self):
                raise RuntimeError("cannot introspect")

        b = _bridge()
        b._handle_event(_open_ev(_Odd()))
        assert b.ready is True

    def test_detach_right_after_attach_clears_ready(self, fake_event):
        """A rejection landing after the echo must un-set ready."""
        b = _bridge()
        b._handle_event(_open_ev(_Receiver()))
        assert b.ready is True
        b._handle_event(_close_ev())
        assert b.ready is False, "probe must not report OK on a cancelled subscription"
        assert b._closed is True

    def test_detach_long_after_attach_keeps_ready(self, fake_event):
        """A healthy session that ends later is not retroactively a failure."""
        b = _bridge()
        b._handle_event(_open_ev(_Receiver()))
        b.attached_at -= 60.0  # pretend the session ran a minute
        b._handle_event(_close_ev())
        assert b.ready is True
        assert b._closed is True
