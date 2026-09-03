"""Unit tests for EventMeshMQTTBrokerClient.

The client drives an asyncio ``amqtt.client.MQTTClient`` on a private event
loop in a daemon thread. We patch ``amqtt.client.MQTTClient`` (where it is
imported from) with an in-process fake so no real broker/network is touched,
then exercise the public API from the calling thread.
"""

from __future__ import annotations

import collections
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from agent_sdk.layer4_frameworks.messaging.event_mesh_mqtt_broker_client import (
    EventMeshMQTTBrokerClient,
)

_MODULE = "agent_sdk.layer4_frameworks.messaging.event_mesh_mqtt_broker_client"


class _FakeMessage:
    def __init__(self, topic: str, data: bytes, packet_id: int = 1) -> None:
        payload = MagicMock()
        payload.data = data
        variable_header = MagicMock()
        variable_header.topic_name = topic
        variable_header.packet_id = packet_id
        packet = MagicMock()
        packet.variable_header = variable_header
        packet.payload = payload
        self.publish_packet = packet


class _FakeMQTTClient:
    """Async fake matching the subset of amqtt.MQTTClient the code uses."""

    def __init__(self, client_id=None, config=None) -> None:
        self.client_id = client_id
        self.config = config
        self.connected = False
        self.connect_calls: list[dict] = []
        self.published: list[tuple] = []
        self.subscribed: list[list] = []
        self.subscribe_result: list[int] = [1]
        self.disconnect_count = 0
        self._inbox: collections.deque = collections.deque()
        self._raises: list[Exception] = []
        self._publish_raises: list[Exception] = []
        self._lock = threading.Lock()

    # --- injection helpers (called from the test thread) ---------------
    def push(self, topic: str, data: bytes, packet_id: int = 1) -> None:
        with self._lock:
            self._inbox.append(_FakeMessage(topic, data, packet_id))

    def fail_next_deliver(self, exc: Exception) -> None:
        with self._lock:
            self._raises.append(exc)

    def fail_next_publish(self, exc: Exception) -> None:
        self._publish_raises.append(exc)

    # --- amqtt.MQTTClient surface --------------------------------------
    async def connect(self, uri, additional_headers=None):
        self.connected = True
        self.connect_calls.append(
            {"uri": uri, "additional_headers": additional_headers or {}}
        )

    async def disconnect(self):
        self.connected = False
        self.disconnect_count += 1

    async def publish(self, topic, payload, qos=None):
        if self._publish_raises:
            raise self._publish_raises.pop(0)
        self.published.append((topic, payload, qos))

    async def subscribe(self, topics):
        self.subscribed.append(list(topics))
        return list(self.subscribe_result)

    async def deliver_message(self, timeout_duration=None):
        import asyncio

        while True:
            with self._lock:
                if self._raises:
                    raise self._raises.pop(0)
                if self._inbox:
                    return self._inbox.popleft()
            await asyncio.sleep(0.005)


class _ClientFactory:
    """Yields pre-seeded fake clients in order, then fresh ones."""

    def __init__(self, *clients: _FakeMQTTClient) -> None:
        self._queue = list(clients)
        self.created: list[_FakeMQTTClient] = []

    def __call__(self, client_id=None, config=None) -> _FakeMQTTClient:
        client = self._queue.pop(0) if self._queue else _FakeMQTTClient()
        client.client_id = client_id
        client.config = config
        self.created.append(client)
        return client


def _wait_until(predicate, timeout=5.0, interval=0.02) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _make_client(**overrides) -> EventMeshMQTTBrokerClient:
    kwargs = dict(
        token_url="",
        messaging_url="mqtts://broker.example:8883",
        management_url="",  # no provisioner -> no HTTP
        client_id="cid",
        client_secret="secret",
        namespace="default",
        messaging_protocol="mqtt311",
        auth_mode="basic",  # password = client_secret, no token HTTP
        connect_retry_attempts=1,
        connect_retry_initial_delay_seconds=0.0,
        connect_retry_max_delay_seconds=0.0,
        publish_retry_attempts=2,
        publish_retry_initial_delay_seconds=0.0,
        publish_retry_max_delay_seconds=0.0,
    )
    kwargs.update(overrides)
    return EventMeshMQTTBrokerClient(**kwargs)


# --------------------------------------------------------------------------
# B4 — config normalization must not falsy-coerce valid zero configs
# --------------------------------------------------------------------------
def test_b4_zero_qos_and_keepalive_are_preserved():
    client = _make_client(
        qos=0,
        keepalive_seconds=0,
        last_will_topic="lw/topic",
        last_will_message="bye",
        last_will_qos=0,
    )
    try:
        assert client._qos == 0
        assert client._keepalive_seconds == 0
        assert client._last_will_qos == 0
        cfg = client._mqtt_client_config()
        assert cfg["default_qos"] == 0
        assert cfg["keep_alive"] == 0
        assert cfg["will"]["qos"] == 0
    finally:
        client.close()


def test_b4_defaults_when_not_specified():
    client = _make_client()
    try:
        assert client._qos == 1
        assert client._keepalive_seconds == 60
        assert client._last_will_qos == 1
    finally:
        client.close()


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------
def test_publish_to_topic_serializes_dict_and_uses_qos():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client(qos=2)
        try:
            client.publish_to_topic("orders", {"id": 7})
            assert len(fake.published) == 1
            topic, payload, qos = fake.published[0]
            assert topic == "default/orders"
            assert payload == b'{"id": 7}'
            assert qos == 2
        finally:
            client.close()


# --------------------------------------------------------------------------
# subscribe — SUBACK grant codes
# --------------------------------------------------------------------------
def test_subscribe_success_registers_callback_and_starts_receiver():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_queue("q", "t", lambda p, h, q: None)
            assert fake.subscribed == [[("q", 1)]]
            assert client._receiver_task is not None
        finally:
            client.close()


def test_subscribe_refusal_0x80_raises():
    fake = _FakeMQTTClient()
    fake.subscribe_result = [0x80]
    factory = _ClientFactory(fake)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            with pytest.raises(RuntimeError, match="refused"):
                client.subscribe_to_queue("q", "t", lambda p, h, q: None)
        finally:
            client.close()


# --------------------------------------------------------------------------
# B1 — a raising callback must NOT kill the receiver; later messages dispatch
# --------------------------------------------------------------------------
def test_b1_callback_exception_does_not_kill_receiver():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    received: list = []

    def callback(payload, headers, queue_name):
        received.append(payload)
        if payload == "boom":
            raise ValueError("callback blew up")

    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_queue("q", "t", callback)
            fake.push("q", b'"boom"')
            fake.push("q", b'"ok"')
            assert _wait_until(lambda: received == ["boom", "ok"]), received
            # Receiver survived the exception and is still running.
            assert not client._receiver_task.done()
        finally:
            client.close()


# --------------------------------------------------------------------------
# B2 — receiver re-acquires the current client after a publish-triggered reset
# --------------------------------------------------------------------------
def test_b2_receiver_reacquires_client_after_reset():
    client1 = _FakeMQTTClient()
    client2 = _FakeMQTTClient()
    client1.fail_next_publish(ConnectionError("publish boom"))
    factory = _ClientFactory(client1, client2)
    received: list = []

    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_queue("q", "t", lambda p, h, q: received.append(p))
            # Publish fails on client1 -> reset -> reconnect as client2.
            client.publish_to_topic("t", {"n": 1})
            assert _wait_until(lambda: len(factory.created) >= 2)
            assert client2.published, "publish should have retried on new client"

            # A message delivered on the NEW client must still reach callbacks,
            # proving the receiver stopped polling the stale client1.
            client2.push("q", b'"after-reset"')
            assert _wait_until(lambda: "after-reset" in received), received
        finally:
            client.close()


# --------------------------------------------------------------------------
# Robustness — token refresh + client rebuild on receiver auth/connection loss
# --------------------------------------------------------------------------
def test_receiver_refreshes_token_and_rebuilds_client_on_failure():
    client1 = _FakeMQTTClient()
    client2 = _FakeMQTTClient()
    client1.fail_next_deliver(ConnectionResetError("401 / connection dropped"))
    factory = _ClientFactory(client1, client2)

    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client(
            messaging_protocol="mqtt311ws", auth_mode="oauth2", token_url="x"
        )
        # Avoid real token HTTP; every call returns a token.
        client._get_access_token = MagicMock(return_value="tok")
        try:
            client.subscribe_to_queue("q", "t", lambda p, h, q: None)
            # After the deliver failure the loop must rebuild the client.
            assert _wait_until(lambda: len(factory.created) >= 2), factory.created
            # Fresh WS client connected with a Bearer header.
            assert client2.connect_calls
            headers = client2.connect_calls[-1]["additional_headers"]
            assert headers.get("Authorization") == "Bearer tok"
            # A forced token refresh was attempted on failure.
            assert any(
                call.kwargs.get("force_refresh") is True
                for call in client._get_access_token.call_args_list
            )
        finally:
            client.close()


# --------------------------------------------------------------------------
# close() must cancel the receiver task (no "Task was destroyed" warnings)
# --------------------------------------------------------------------------
def test_close_cancels_receiver_task():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        client.subscribe_to_queue("q", "t", lambda p, h, q: None)
        task = client._receiver_task
        assert task is not None
        client.close()
        assert task.done()
        assert fake.disconnect_count >= 1


# --------------------------------------------------------------------------
# MINI-2 — ack/nack/reject are documented no-ops (best-effort at-most-once)
# --------------------------------------------------------------------------
def test_ack_nack_reject_are_noops():
    client = _make_client()
    try:
        assert client.ack_message("q", {"x-message-id": "1"}) is None
        assert client.nack_message("q", {"x-message-id": "1"}, requeue=True) is None
        assert client.reject_message("q", {"x-message-id": "1"}, requeue=False) is None
    finally:
        client.close()


def test_mini2_best_effort_semantics_documented():
    # The delivery guarantee must be stated on the class and the nack path so
    # callers are not misled into assuming redelivery on failure.
    assert "at-most-once" in EventMeshMQTTBrokerClient.__doc__


# --------------------------------------------------------------------------
# Client-creation race — a single lock serializes creation across coroutines
# --------------------------------------------------------------------------
def test_ensure_client_reuses_single_instance_under_concurrency():
    factory = _ClientFactory(_FakeMQTTClient(), _FakeMQTTClient())
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            import asyncio

            async def _race():
                results = await asyncio.gather(
                    client._ensure_client_async(),
                    client._ensure_client_async(),
                    client._ensure_client_async(),
                )
                return results

            results = client._run_coro_sync(_race())
            assert all(r is results[0] for r in results)
            assert len(factory.created) == 1
        finally:
            client.close()


# --------------------------------------------------------------------------
# B1 (second half) — the receiver done-callback restarts a crashed loop
# --------------------------------------------------------------------------
def test_on_receiver_done_restarts_on_unexpected_exception():
    factory = _ClientFactory(_FakeMQTTClient(), _FakeMQTTClient())
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            import asyncio

            client._ensure_loop()
            client._running = True
            task = MagicMock()
            task.cancelled.return_value = False
            task.exception.return_value = RuntimeError("receiver died")

            # Run inside the private loop so ensure_future has a running loop.
            async def _call():
                client._on_receiver_done(task)

            client._run_coro_sync(_call())
            assert isinstance(client._receiver_task, asyncio.Future)
        finally:
            client.close()


def test_on_receiver_done_noop_on_cancel_or_clean_exit():
    client = _make_client()
    try:
        cancelled = MagicMock()
        cancelled.cancelled.return_value = True
        client._on_receiver_done(cancelled)  # must not raise / restart

        clean = MagicMock()
        clean.cancelled.return_value = False
        clean.exception.return_value = None
        client._on_receiver_done(clean)  # clean exit -> no restart

        assert client._receiver_task is None
    finally:
        client.close()


# --------------------------------------------------------------------------
# publish — string payload + OAuth token refresh on reconnect
# --------------------------------------------------------------------------
def test_publish_string_message_and_oauth_token_refresh_on_retry():
    client1 = _FakeMQTTClient()
    client2 = _FakeMQTTClient()
    client1.fail_next_publish(ConnectionError("publish boom"))
    factory = _ClientFactory(client1, client2)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client(auth_mode="oauth2", token_url="x")
        client._get_access_token = MagicMock(return_value="tok")
        try:
            client.publish_to_topic("t", "hello")
            assert client2.published
            _topic, payload, _qos = client2.published[0]
            assert payload == b"hello"
            assert any(
                call.kwargs.get("force_refresh") is True
                for call in client._get_access_token.call_args_list
            )
        finally:
            client.close()


# --------------------------------------------------------------------------
# OAuth token acquisition
# --------------------------------------------------------------------------
def test_get_access_token_non_oauth_returns_secret():
    client = _make_client(auth_mode="basic")
    try:
        assert client._get_access_token() == "secret"
    finally:
        client.close()


def test_get_access_token_oauth_success_is_cached():
    client = _make_client(auth_mode="oauth2", token_url="https://token")
    resp = MagicMock()
    resp.json.return_value = {"access_token": "abc", "expires_in": 300}
    resp.raise_for_status.return_value = None
    client._http = MagicMock()
    client._http.post.return_value = resp
    try:
        assert client._get_access_token() == "abc"
        assert client._get_access_token() == "abc"  # served from cache
        assert client._http.post.call_count == 1
    finally:
        client.close()


def test_get_access_token_missing_token_raises():
    client = _make_client(auth_mode="oauth2", token_url="https://token")
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


# --------------------------------------------------------------------------
# WebSocket Bearer header injection (mqtt311ws)
# --------------------------------------------------------------------------
def test_ws_headers_build_bearer_and_strip_prefix_and_whitespace():
    client = _make_client(
        messaging_protocol="mqtt311ws", auth_mode="oauth2", token_url="x"
    )
    client._get_access_token = MagicMock(return_value="bearer  tok 123")
    try:
        assert client._mqtt_ws_additional_headers() == {
            "Authorization": "Bearer tok123"
        }
    finally:
        client.close()


def test_ws_headers_empty_for_non_ws_protocol():
    client = _make_client(messaging_protocol="mqtt311", auth_mode="oauth2")
    try:
        assert client._mqtt_ws_additional_headers() == {}
    finally:
        client.close()


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------
def test_uri_with_credentials_encodes_and_skips_when_present():
    cls = EventMeshMQTTBrokerClient
    uri = "wss://host:443/mqtt"
    out = cls._uri_with_credentials(uri, "us er", "p@ss/word")
    assert "us%20er:p%40ss%2Fword@host:443" in out
    assert cls._uri_with_credentials(uri, "", "x") == uri
    assert (
        cls._uri_with_credentials("wss://u:p@host/mqtt", "a", "b")
        == "wss://u:p@host/mqtt"
    )


def test_normalize_mqtt_client_id():
    cls = EventMeshMQTTBrokerClient
    assert cls._normalize_mqtt_client_id("cid-123!") == "cid123"
    long_id = cls._normalize_mqtt_client_id("x" * 50)
    assert long_id.startswith("sm") and 1 <= len(long_id) <= 23
    hashed = cls._normalize_mqtt_client_id("!!!")
    assert hashed.startswith("sm")


def test_check_subscribe_result_variants():
    cls = EventMeshMQTTBrokerClient
    cls._check_subscribe_result([1], "a")
    cls._check_subscribe_result([], "a")
    cls._check_subscribe_result(None, "a")
    with pytest.raises(RuntimeError, match="refused"):
        cls._check_subscribe_result([0x80], "a")


def test_retryable_http_and_delay_bounds():
    import httpx

    cls = EventMeshMQTTBrokerClient
    assert cls._is_retryable_http_exception(httpx.ConnectError("x")) is True

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


def test_normalize_mqtt_uri_adds_scheme_when_missing():
    plain = _make_client(
        messaging_url="broker.example.com", messaging_protocol="mqtt311"
    )
    ws = _make_client(
        messaging_url="broker.example.com", messaging_protocol="mqtt311ws"
    )
    try:
        assert plain._normalize_mqtt_uri() == "mqtts://broker.example.com"
        assert ws._normalize_mqtt_uri() == "wss://broker.example.com"
        # A URL that already has a scheme is returned unchanged.
        scheme_present = _make_client(messaging_url="wss://x:443")
        assert scheme_present._normalize_mqtt_uri() == "wss://x:443"
        scheme_present.close()
    finally:
        plain.close()
        ws.close()


def test_subscribe_to_topic_and_topics_dedupe_and_register():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_topic("orders", lambda p, h, q: None)
            # subscribe_to_topic maps topic -> queue "<ns>/<topic>".
            assert fake.subscribed[-1] == [("default/orders", 1)]

            # Blank/duplicate topics are de-duplicated; only unique ones subscribe.
            client.subscribe_to_topics(["a", "a", " ", "b"], lambda p, h, q: None)
            subscribed_addrs = [entry[0][0] for entry in fake.subscribed]
            assert "default/a" in subscribed_addrs
            assert "default/b" in subscribed_addrs
            assert subscribed_addrs.count("default/a") == 1
        finally:
            client.close()


def test_ensure_queue_delegates_to_provisioner():
    client = _make_client()
    provisioner = MagicMock()
    client._provisioner = provisioner
    try:
        client.ensure_queue("q")
        client.ensure_queue_subscription("q", "default/t")
        provisioner.ensure_queue.assert_called_once_with("q")
        provisioner.ensure_queue_subscription.assert_called_once_with("q", "default/t")
    finally:
        client._provisioner = None
        client.close()


def test_get_access_token_retries_retryable_then_succeeds():
    client = _make_client(
        auth_mode="oauth2",
        token_url="https://token",
        connect_retry_attempts=2,
        connect_retry_initial_delay_seconds=0.0,
        connect_retry_max_delay_seconds=0.0,
    )
    import httpx

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


def test_build_queue_name_uses_namespace():
    client = _make_client(namespace="ns")
    try:
        assert client.build_queue_name("orders") == "ns/orders"
    finally:
        client.close()


def test_connect_retries_then_raises():
    class _Boom(_FakeMQTTClient):
        async def connect(self, uri, additional_headers=None):
            raise ConnectionError("connect refused")

    factory = _ClientFactory(_Boom(), _Boom())
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client(connect_retry_attempts=2)
        try:
            with pytest.raises(ConnectionError):
                client._run_coro_sync(client._ensure_client_async())
            assert len(factory.created) == 2  # one instance per retry attempt
        finally:
            client.close()


# --------------------------------------------------------------------------
# Bonus boss — a QUEUE subscription whose name differs from the topic must
# still deliver: the broker delivers on the topic path, not the queue address.
# --------------------------------------------------------------------------
def test_subscribe_to_queue_delivers_when_queue_name_differs_from_topic():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    received: list = []

    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            # Queue name deliberately unrelated to the topic.
            client.subscribe_to_queue(
                "orders-queue", "orders-topic", lambda p, h, q: received.append((q, p))
            )
            # The wire subscription is on the queue address...
            assert fake.subscribed == [[("orders-queue", 1)]]
            # ...but the broker delivers the message on the topic path
            # (namespace/topic), which is NOT the queue address. Before the fix
            # this message was silently dropped.
            fake.push("default/orders-topic", b'"payload"')
            assert _wait_until(
                lambda: received == [("orders-queue", "payload")]
            ), received
        finally:
            client.close()


def test_mqtt_topic_matches_supports_wildcards():
    cls = EventMeshMQTTBrokerClient
    assert cls._mqtt_topic_matches("a/b/c", "a/b/c") is True
    assert cls._mqtt_topic_matches("a/+/c", "a/x/c") is True
    assert cls._mqtt_topic_matches("a/+/c", "a/x/y") is False
    assert cls._mqtt_topic_matches("a/#", "a/b/c/d") is True
    assert cls._mqtt_topic_matches("a/b", "a/b/c") is False
    assert cls._mqtt_topic_matches("a/+", "a") is False


def test_two_arg_and_async_callbacks_are_dispatched():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    received: list = []

    async def async_two_arg_cb(payload, headers):  # 2-arg + awaitable
        received.append(payload)

    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_queue("q", "t", async_two_arg_cb)
            fake.push("q", b'"hi"')
            assert _wait_until(lambda: "hi" in received), received
        finally:
            client.close()


# --------------------------------------------------------------------------
# BOSS-2 (final) — reconnect MUST re-subscribe existing queues.
#
# clean_session means a reconnected client has NO subscriptions, so delivery
# would silently die after the first reconnect. NOTE: the fake delivers pushed
# messages regardless of subscription state, so a "delivery still works" check
# would pass even with the bug present (this is exactly how the bug hid). The
# real proof is that the NEW client actually received the SUBSCRIBE.
# --------------------------------------------------------------------------
def test_reconnect_resubscribes_existing_queues():
    client1 = _FakeMQTTClient()
    client2 = _FakeMQTTClient()
    client1.fail_next_publish(ConnectionError("publish boom"))
    factory = _ClientFactory(client1, client2)

    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_queue(
                "orders-queue", "orders-topic", lambda p, h, q: None
            )
            assert client1.subscribed == [[("orders-queue", 1)]]

            # A failed publish forces reset -> reconnect as client2.
            client.publish_to_topic("orders-topic", {"n": 1})
            assert _wait_until(lambda: len(factory.created) >= 2), factory.created

            # The receiver must re-apply the subscription on the fresh client.
            assert _wait_until(
                lambda: [("orders-queue", 1)] in client2.subscribed
            ), client2.subscribed
        finally:
            client.close()


def test_no_double_subscribe_on_same_client():
    fake = _FakeMQTTClient()
    factory = _ClientFactory(fake)
    with patch("amqtt.client.MQTTClient", new=factory):
        client = _make_client()
        try:
            client.subscribe_to_queue("q1", "t1", lambda p, h, q: None)
            client.subscribe_to_queue("q2", "t2", lambda p, h, q: None)
            # Let the receiver loop iterate several times.
            time.sleep(0.2)
            flat = [entry for call in fake.subscribed for entry in call]
            assert flat.count(("q1", 1)) == 1, fake.subscribed
            assert flat.count(("q2", 1)) == 1, fake.subscribed
        finally:
            client.close()


# --------------------------------------------------------------------------
# QoS / range validation at the client boundary (raise, don't silently coerce)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [-1, 3, 5])
def test_qos_out_of_range_raises(bad):
    with pytest.raises(ValueError):
        _make_client(qos=bad)


@pytest.mark.parametrize("bad", [-1, 3])
def test_last_will_qos_out_of_range_raises(bad):
    with pytest.raises(ValueError):
        _make_client(last_will_qos=bad)


@pytest.mark.parametrize("good", [0, 1, 2])
def test_qos_valid_values_accepted(good):
    client = _make_client(qos=good)
    assert client._qos == good
    client.close()


def test_keepalive_negative_raises():
    with pytest.raises(ValueError):
        _make_client(keepalive_seconds=-1)


def test_connect_retry_attempts_must_be_positive():
    with pytest.raises(ValueError):
        _make_client(connect_retry_attempts=0)
