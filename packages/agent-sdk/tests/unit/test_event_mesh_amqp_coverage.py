"""Coverage-focused unit tests for EventMeshAMQPBrokerClient.

These exercise the pure helpers and internal state machines (delivery-tag
coercion, settlement marshalling, endpoint/hostname parsing, client kwargs,
body decoding, token acquisition, publish retry exhaustion) without touching a
real broker. They follow the fake-client mocking style of
``test_event_mesh_amqp_send_client_cache.py`` and ``test_event_mesh_amqp_consume.py``.
"""

from __future__ import annotations

import queue
import threading
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from agent_sdk.layer4_frameworks.messaging.event_mesh_amqp_broker_client import (
    EventMeshAMQPBrokerClient,
    _PyAMQPDelivery,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _bare(**overrides: Any) -> EventMeshAMQPBrokerClient:
    kwargs = dict(
        token_url="",
        messaging_url="wss://messaging.example.com:443/protocols/amqp10ws",
        management_url="",
        client_id="cid",
        client_secret="sec",
        namespace="ns",
        auth_mode="basic",
    )
    kwargs.update(overrides)
    return EventMeshAMQPBrokerClient(**kwargs)


class _SettleClient:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any, str]] = []

    def settle_messages(
        self, delivery_id: Any, delivery_tag: Any, outcome: str
    ) -> None:
        self.calls.append((delivery_id, delivery_tag, outcome))


class _Frame:
    def __init__(self, delivery_id: Any, delivery_tag: Any) -> None:
        self.delivery_id = delivery_id
        self.delivery_tag = delivery_tag


# --------------------------------------------------------------------------
# _PyAMQPDelivery.delivery-tag coercion + settlement
# --------------------------------------------------------------------------
def test_coerce_delivery_tag_variants() -> None:
    coerce = _PyAMQPDelivery._coerce_delivery_tag
    assert coerce(None) is None
    assert coerce(b"x") == b"x"
    assert coerce(bytearray(b"y")) == b"y"
    assert coerce(memoryview(b"z")) == b"z"
    assert coerce(5) is None  # int is unsupported -> warns, returns None
    assert coerce("s") == b"s"
    assert coerce([1, 2, 3]) == bytes([1, 2, 3])  # bytes() fallback

    class _HasToBytes:
        def tobytes(self) -> bytes:
            return b"tb"

    assert coerce(_HasToBytes()) == b"tb"

    class _ToBytesRaises:
        def tobytes(self) -> bytes:
            raise ValueError("nope")

    # tobytes() raises, and bytes(obj) also fails -> returns None.
    assert coerce(_ToBytesRaises()) is None


def test_delivery_settle_missing_ids_is_marked_settled() -> None:
    client = _SettleClient()
    delivery = _PyAMQPDelivery(
        client=client,
        frame=object(),  # no delivery_id / delivery_tag anywhere
        message=None,
        settlement_lock=threading.RLock(),
        queue_name="q",
    )
    delivery.settle("accepted")
    assert client.calls == []
    assert delivery._settled is True


def test_delivery_accept_release_reject_and_double_settle() -> None:
    client = _SettleClient()
    delivery = _PyAMQPDelivery(
        client=client,
        frame=_Frame(delivery_id=7, delivery_tag=b"tag"),
        message=None,
        settlement_lock=threading.RLock(),
        queue_name="q",
    )
    delivery.accept()
    assert client.calls == [(7, b"tag", "accepted")]
    # Already settled -> release/reject are no-ops.
    delivery.release()
    delivery.reject()
    assert len(client.calls) == 1


def test_frame_value_reads_from_dict_and_tuple_index() -> None:
    client = _SettleClient()
    dict_delivery = _PyAMQPDelivery(
        client=client,
        frame={"delivery_id": 3, "delivery_tag": b"k"},
        message=None,
        settlement_lock=threading.RLock(),
    )
    assert dict_delivery.delivery_id == 3
    assert dict_delivery.delivery_tag == b"k"

    tuple_delivery = _PyAMQPDelivery(
        client=client,
        frame=(None, 9, b"m"),
        message=None,
        settlement_lock=threading.RLock(),
    )
    assert tuple_delivery.delivery_id == 9
    assert tuple_delivery.delivery_tag == b"m"


# --------------------------------------------------------------------------
# retry / outcome pure helpers
# --------------------------------------------------------------------------
def test_is_retryable_http_exception_and_delay_bounds() -> None:
    cls = EventMeshAMQPBrokerClient
    assert cls._is_retryable_http_exception(httpx.ConnectError("x")) is True
    assert cls._is_retryable_http_exception(httpx.ReadError("x")) is True

    resp = MagicMock()
    resp.status_code = 503
    assert (
        cls._is_retryable_http_exception(
            httpx.HTTPStatusError("x", request=MagicMock(), response=resp)
        )
        is True
    )
    resp2 = MagicMock()
    resp2.status_code = 404
    assert (
        cls._is_retryable_http_exception(
            httpx.HTTPStatusError("x", request=MagicMock(), response=resp2)
        )
        is False
    )
    assert cls._is_retryable_http_exception(ValueError()) is False

    delay = cls._retry_delay_seconds(2, initial=0.1, maximum=1.0)
    assert 0.0 <= delay <= 1.0
    assert cls._retry_delay_seconds(1, initial=0.0, maximum=0.0) == 0.0


def test_normalize_outcome_aliases() -> None:
    n = EventMeshAMQPBrokerClient._normalize_outcome
    assert n("ack", "released") == "accepted"
    assert n("accept", "released") == "accepted"
    assert n("requeue", "accepted") == "released"
    assert n("reject", "accepted") == "rejected"
    assert n("", "released") == "released"
    assert n("garbage", "accepted") == "accepted"


# --------------------------------------------------------------------------
# token acquisition
# --------------------------------------------------------------------------
def test_get_access_token_non_oauth_returns_secret() -> None:
    client = _bare(auth_mode="basic")
    try:
        assert client._get_access_token() == "sec"
    finally:
        client.close()


def test_get_access_token_no_token_url_returns_secret() -> None:
    client = _bare(auth_mode="oauth2", token_url="")
    try:
        assert client._get_access_token() == "sec"
    finally:
        client.close()


def test_get_access_token_oauth_success_is_cached() -> None:
    client = _bare(auth_mode="oauth2", token_url="https://token")
    resp = MagicMock()
    resp.json.return_value = {"access_token": "abc", "expires_in": 300}
    resp.raise_for_status.return_value = None
    client._http = MagicMock()
    client._http.post.return_value = resp
    try:
        assert client._get_access_token() == "abc"
        assert client._get_access_token() == "abc"  # cached
        assert client._http.post.call_count == 1
    finally:
        client.close()


def test_get_access_token_missing_token_raises() -> None:
    client = _bare(auth_mode="oauth2", token_url="https://token")
    resp = MagicMock()
    resp.json.return_value = {}
    resp.raise_for_status.return_value = None
    client._http = MagicMock()
    client._http.post.return_value = resp
    try:
        with pytest.raises(Exception, match="access_token"):
            client._get_access_token()
    finally:
        client.close()


def test_get_access_token_retries_retryable_then_succeeds() -> None:
    client = _bare(
        auth_mode="oauth2",
        token_url="https://token",
        token_retry_attempts=2,
        token_retry_initial_delay_seconds=0.0,
        token_retry_max_delay_seconds=0.0,
    )
    ok = MagicMock()
    ok.json.return_value = {"access_token": "abc", "expires_in": 300}
    ok.raise_for_status.return_value = None
    client._http = MagicMock()
    client._http.post.side_effect = [httpx.ConnectError("boom"), ok]
    try:
        assert client._get_access_token() == "abc"
        assert client._http.post.call_count == 2
    finally:
        client.close()


def test_get_access_token_non_retryable_breaks_immediately() -> None:
    client = _bare(
        auth_mode="oauth2", token_url="https://token", token_retry_attempts=3
    )
    client._http = MagicMock()
    client._http.post.side_effect = ValueError("bad")
    try:
        with pytest.raises(ValueError):
            client._get_access_token()
        # Non-retryable error must not spin through all attempts.
        assert client._http.post.call_count == 1
    finally:
        client.close()


# --------------------------------------------------------------------------
# endpoint / hostname parsing
# --------------------------------------------------------------------------
def test_endpoint_parts_websocket_with_query() -> None:
    client = _bare(
        messaging_url="wss://h.example.com/protocols/amqp10ws?x=1",
        messaging_protocol="amqp10ws",
    )
    try:
        host, port, websocket, path = client._endpoint_parts()
        assert host == "h.example.com"
        assert port == 443
        assert websocket is True
        assert path == "/protocols/amqp10ws?x=1"
    finally:
        client.close()


def test_endpoint_parts_websocket_default_path_added() -> None:
    client = _bare(messaging_url="wss://h.example.com", messaging_protocol="amqp10ws")
    try:
        host, port, websocket, path = client._endpoint_parts()
        assert (host, port, websocket, path) == (
            "h.example.com",
            443,
            True,
            "/protocols/amqp10ws",
        )
    finally:
        client.close()


def test_endpoint_parts_plain_amqp_default_port() -> None:
    client = _bare(messaging_url="amqp://h.example.com", messaging_protocol="amqp10")
    try:
        host, port, websocket, path = client._endpoint_parts()
        assert host == "h.example.com"
        assert port == 5672
        assert websocket is False
    finally:
        client.close()


def test_endpoint_parts_invalid_url_raises() -> None:
    client = _bare(messaging_url="not-a-url", messaging_protocol="amqp10")
    try:
        with pytest.raises(RuntimeError, match="Invalid Event Mesh AMQP URL"):
            client._endpoint_parts()
    finally:
        client.close()


def test_hostname_strips_scheme_and_default_port() -> None:
    client = _bare(
        messaging_url="wss://h.example.com:443/protocols/amqp10ws",
        messaging_protocol="amqp10ws",
    )
    try:
        # default ws port is omitted; path preserved.
        assert client._hostname() == "h.example.com/protocols/amqp10ws"
    finally:
        client.close()


def test_hostname_non_default_port_and_added_default_path() -> None:
    client = _bare(
        messaging_url="wss://h.example.com:8443", messaging_protocol="amqp10ws"
    )
    try:
        assert client._hostname() == "h.example.com:8443/protocols/amqp10ws"
    finally:
        client.close()


def test_hostname_ipv6_and_plain_protocol() -> None:
    client = _bare(
        messaging_url="amqps://[2001:db8::1]:5671", messaging_protocol="amqp10"
    )
    try:
        # plain amqp10 returns authority only (default port 5671 dropped).
        assert client._hostname() == "[2001:db8::1]"
    finally:
        client.close()


def test_hostname_empty_returns_empty() -> None:
    client = _bare(messaging_url="", messaging_protocol="amqp10ws")
    try:
        assert client._hostname() == ""
    finally:
        client.close()


def test_custom_endpoint_websocket_vs_plain() -> None:
    ws_client = _bare(
        messaging_url="wss://h.example.com:443/protocols/amqp10ws",
        messaging_protocol="amqp10ws",
    )
    plain_client = _bare(
        messaging_url="amqp://h.example.com:5672", messaging_protocol="amqp10"
    )
    try:
        assert ws_client._custom_endpoint() == "h.example.com:443/protocols/amqp10ws"
        assert plain_client._custom_endpoint() is None
    finally:
        ws_client.close()
        plain_client.close()


def test_ssl_context_verify_toggle() -> None:
    import ssl

    verify = _bare(verify_ssl=True)
    noverify = _bare(verify_ssl=False)
    try:
        assert verify._ssl_context().verify_mode != ssl.CERT_NONE
        ctx = noverify._ssl_context()
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
    finally:
        verify.close()
        noverify.close()


# --------------------------------------------------------------------------
# _client_kwargs — both auth branches (uses the real vendored _pyamqp)
# --------------------------------------------------------------------------
def test_client_kwargs_websocket_bearer_uses_anonymous_sasl() -> None:
    client = _bare(
        messaging_url="wss://h.example.com:443/protocols/amqp10ws",
        messaging_protocol="amqp10ws",
        auth_mode="oauth2",
        token_url="https://token",
    )
    client._get_access_token = lambda *, force_refresh=False: "tok"  # type: ignore[assignment]
    try:
        kwargs = client._client_kwargs()
        assert (
            kwargs["custom_endpoint_address"] == "h.example.com:443/protocols/amqp10ws"
        )
        auth = kwargs["auth"]
        assert getattr(auth, "auth_type", "") == "sasl_anonymous"
        assert kwargs["socket_timeout"] == client._poll_timeout_seconds
    finally:
        client.close()


def test_client_kwargs_plain_amqp_uses_sasl_plain() -> None:
    client = _bare(
        messaging_url="amqp://h.example.com:5672",
        messaging_protocol="amqp10",
        auth_mode="basic",
    )
    try:
        kwargs = client._client_kwargs()
        assert "custom_endpoint_address" not in kwargs  # not a websocket endpoint
        assert kwargs["auth"] is not None
    finally:
        client.close()


# --------------------------------------------------------------------------
# address building + provisioner delegation
# --------------------------------------------------------------------------
def test_address_helpers() -> None:
    client = _bare(namespace="ns")
    try:
        assert client.build_queue_name("orders") == "ns/orders"
        assert client._topic_address("orders") == "topic:ns/orders"
        assert client._queue_address("ns/orders") == "queue:ns/orders"
    finally:
        client.close()


def test_ensure_queue_and_subscription_delegate_to_provisioner() -> None:
    client = _bare()
    provisioner = MagicMock()
    client._provisioner = provisioner
    try:
        client.ensure_queue("q")
        client.ensure_queue_subscription("q", "ns/topic")
        provisioner.ensure_queue.assert_called_once_with("q")
        provisioner.ensure_queue_subscription.assert_called_once_with("q", "ns/topic")
    finally:
        client._provisioner = None
        client.close()


def test_ensure_queue_noop_without_provisioner() -> None:
    client = _bare()
    try:
        # No provisioner configured -> silently does nothing.
        client.ensure_queue("q")
        client.ensure_queue_subscription("q", "t")
    finally:
        client.close()


# --------------------------------------------------------------------------
# body decoding
# --------------------------------------------------------------------------
def test_decode_body_variants() -> None:
    decode = EventMeshAMQPBrokerClient._decode_body

    class _Msg:
        def __init__(self, **kw: Any) -> None:
            for k, v in kw.items():
                setattr(self, k, v)

    assert decode(_Msg(data=b'{"a": 1}')) == {"a": 1}
    assert decode(_Msg(data="plain-text")) == "plain-text"
    assert decode(_Msg(data=[b'{"x":', b" 2}"])) == {"x": 2}
    # No `data`; falls back to `value`.
    assert decode(_Msg(data=None, value=b"hello")) == "hello"
    # Non-iterable body -> str() fallback (then JSON-parsed: "123" -> 123).
    assert decode(_Msg(data=123)) == 123

    class _Weird:
        def __str__(self) -> str:
            return "not-json"

    # Non-iterable, non-JSON body -> str() fallback returns the raw text.
    assert decode(_Msg(data=_Weird())) == "not-json"


def test_split_received_item() -> None:
    split = EventMeshAMQPBrokerClient._split_received_item
    assert split((("frame",), "msg")) == (("frame",), "msg")
    assert split(("frame", "msg")) == ("frame", "msg")
    assert split("just-a-message") == (None, "just-a-message")


# --------------------------------------------------------------------------
# ack / nack / reject header handling
# --------------------------------------------------------------------------
def test_ack_nack_reject_without_delivery_id_are_noops() -> None:
    client = _bare()
    try:
        # No 'x-agent-sdk-amqp-delivery-id' header -> nothing enqueued/raised.
        client.ack_message("q", {})
        client.nack_message("q", {}, requeue=True)
        client.reject_message("q", {}, requeue=False)
        assert client._deliveries == {}
    finally:
        client.close()


# --------------------------------------------------------------------------
# settlement marshalling internals
# --------------------------------------------------------------------------
def test_request_settlement_drops_delivery_when_no_consumer() -> None:
    client = _bare()
    try:
        delivery = _PyAMQPDelivery(
            client=_SettleClient(),
            frame=_Frame(1, b"t"),
            message=None,
            settlement_lock=client._settlement_lock,
            queue_name="q",
        )
        delivery_id = client._register_delivery(delivery)
        # No settlement queue registered for "q" -> tracking entry is dropped.
        client._request_settlement(delivery_id, "accepted")
        assert delivery_id not in client._deliveries
    finally:
        client.close()


def test_request_settlement_enqueues_for_live_consumer() -> None:
    client = _bare()
    try:
        settle_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        client._settlement_queues["q"] = settle_q
        delivery = _PyAMQPDelivery(
            client=_SettleClient(),
            frame=_Frame(1, b"t"),
            message=None,
            settlement_lock=client._settlement_lock,
            queue_name="q",
        )
        delivery_id = client._register_delivery(delivery)
        client._request_settlement(delivery_id, "accepted")
        assert settle_q.get_nowait() == (delivery_id, "accepted")
        # Second request is ignored because settle_requested is already set.
        client._request_settlement(delivery_id, "released")
        assert settle_q.empty()
    finally:
        client.close()


def test_drain_settlement_queue_performs_and_evicts() -> None:
    client = _bare()
    try:
        settle_client = _SettleClient()
        delivery = _PyAMQPDelivery(
            client=settle_client,
            frame=_Frame(5, b"tag5"),
            message=None,
            settlement_lock=client._settlement_lock,
            queue_name="q",
        )
        delivery_id = client._register_delivery(delivery)
        settle_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        settle_q.put((delivery_id, "accepted"))
        client._drain_settlement_queue(settle_q)
        assert settle_client.calls == [(5, b"tag5", "accepted")]
        assert delivery_id not in client._deliveries
    finally:
        client.close()


def test_perform_settlement_missing_delivery_is_noop() -> None:
    client = _bare()
    try:
        client._perform_settlement("does-not-exist", "accepted")  # must not raise
    finally:
        client.close()


def test_on_callback_complete_settles_after_last_callback() -> None:
    client = _bare()
    try:
        settle_q: "queue.Queue[tuple[str, str]]" = queue.Queue()
        client._settlement_queues["q"] = settle_q
        delivery = _PyAMQPDelivery(
            client=_SettleClient(),
            frame=_Frame(1, b"t"),
            message=None,
            settlement_lock=client._settlement_lock,
            queue_name="q",
        )
        delivery.pending = 2
        delivery_id = client._register_delivery(delivery)

        # First completion: still one callback outstanding -> not yet settled.
        client._on_callback_complete(delivery_id, errored=False)
        assert settle_q.empty()
        # Second completion (with an error) -> settle with the error outcome.
        client._on_callback_complete(delivery_id, errored=True)
        enq_id, outcome = settle_q.get_nowait()
        assert enq_id == delivery_id
        assert outcome == client._default_error_settlement_outcome
    finally:
        client.close()


def test_on_callback_complete_unknown_delivery_is_noop() -> None:
    client = _bare()
    try:
        client._on_callback_complete("nope", errored=False)  # must not raise
    finally:
        client.close()


# --------------------------------------------------------------------------
# publish retry exhaustion -> raises the last error
# --------------------------------------------------------------------------
class _AlwaysFailSendClient:
    instances: list["_AlwaysFailSendClient"] = []

    def __init__(self, hostname: str, target: str, **kwargs: Any) -> None:
        self.hostname = hostname
        self.target = target
        self.closed = False
        type(self).instances.append(self)

    def send_message(self, message: Any, *, timeout: float = 0) -> None:
        raise RuntimeError("send always fails")

    def close(self) -> None:
        self.closed = True


class _FakeProperties:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class _FakeMessage:
    def __init__(self, *, data: list[bytes], properties: Any = None) -> None:
        self.data = data
        self.properties = properties


def test_publish_raises_after_exhausting_retries_and_refreshes_token() -> None:
    _AlwaysFailSendClient.instances.clear()
    client = _bare(
        auth_mode="oauth2",
        token_url="https://token",
        publish_retry_attempts=2,
        publish_retry_initial_delay_seconds=0.0,
        publish_retry_max_delay_seconds=0.0,
    )
    refreshes: list[bool] = []
    client._import_pyamqp = lambda: (
        object,
        _AlwaysFailSendClient,
        object,
        object,
        (_FakeMessage, _FakeProperties),
    )
    client._client_kwargs = lambda: {}
    client._hostname = lambda: "h:443"

    def _tok(*, force_refresh: bool = False) -> str:
        refreshes.append(force_refresh)
        return "tok"

    client._get_access_token = _tok  # type: ignore[assignment]
    client._export_amqp_ws_authorization_for_vendor_transport = (
        lambda *, force_refresh=False: None
    )
    try:
        with pytest.raises(RuntimeError, match="send always fails"):
            client.publish_to_topic("orders", {"n": 1})
        # Both attempts created and discarded a sender.
        assert len(_AlwaysFailSendClient.instances) == 2
        assert all(inst.closed for inst in _AlwaysFailSendClient.instances)
        # A forced token refresh happened between the two attempts.
        assert True in refreshes
    finally:
        client.close()
