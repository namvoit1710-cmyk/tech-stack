from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

from agent_sdk.layer4_frameworks.messaging._vendor.azure_pyamqp import (
    _stdlib_websocket as ws,
)
from agent_sdk.layer4_frameworks.messaging.event_mesh_amqp_broker_client import (
    EventMeshAMQPBrokerClient,
)


class _FakeFrame:
    def __init__(self, delivery_id: int, delivery_tag: bytes) -> None:
        self.delivery_id = delivery_id
        self.delivery_tag = delivery_tag


class _FakeMessage:
    def __init__(self, data: list[bytes]) -> None:
        self.data = data


class _FakeReceiveClient:
    """Vendored ReceiveClient stand-in.

    ``receive_messages_iter`` yields whatever is queued in ``pending_items``
    (once), then empty iterators. ``raise_on_receive`` makes the first N receive
    calls raise a non-timeout error to exercise the reconnect path.
    """

    instances: list["_FakeReceiveClient"] = []
    pending_items: list[tuple[Any, Any]] = []
    raise_on_receive = 0
    _cls_lock = threading.Lock()

    def __init__(
        self, hostname: str, source: str, *, link_credit: int = 0, **kwargs: Any
    ) -> None:
        self.hostname = hostname
        self.source = source
        self.link_credit = link_credit
        self.kwargs = kwargs
        self.closed = False
        # (delivery_id, delivery_tag, outcome, thread_name)
        self.settle_calls: list[tuple[Any, Any, str, str]] = []
        self.receive_threads: set[str] = set()
        type(self).instances.append(self)

    def receive_messages_iter(self, timeout: float | None = None):
        cls = type(self)
        self.receive_threads.add(threading.current_thread().name)
        with cls._cls_lock:
            if cls.raise_on_receive > 0:
                cls.raise_on_receive -= 1
                raise RuntimeError("simulated receive failure")
            items = cls.pending_items
            cls.pending_items = []
        return iter(items)

    def settle_messages(
        self, delivery_id: Any, delivery_tag: Any, outcome: str, **kwargs: Any
    ) -> None:
        self.settle_calls.append(
            (delivery_id, delivery_tag, outcome, threading.current_thread().name)
        )

    def close(self) -> None:
        self.closed = True


def _client(**overrides: Any) -> EventMeshAMQPBrokerClient:
    token_refresh_calls: list[bool] = []

    client = EventMeshAMQPBrokerClient(
        token_url="https://token.example.com",
        messaging_url="wss://messaging.example.com/protocols/amqp10ws",
        management_url="",
        client_id="client-id",
        client_secret="client-secret",
        namespace="ns",
        poll_timeout_seconds=0.05,
        **overrides,
    )
    client._import_pyamqp = lambda: (
        object,
        object,
        _FakeReceiveClient,
        object,
        (object, object),
    )
    client._client_kwargs = lambda: {}
    client._hostname = lambda: "messaging.example.com:443"

    def _fake_token(*, force_refresh: bool = False) -> str:
        token_refresh_calls.append(force_refresh)
        return "unit-test-token"

    client._get_access_token = _fake_token  # type: ignore[assignment]
    client._export_amqp_ws_authorization_for_vendor_transport = (
        lambda *, force_refresh=False: None
    )
    client._token_refresh_calls = token_refresh_calls  # type: ignore[attr-defined]
    return client


def setup_function() -> None:
    _FakeReceiveClient.instances.clear()
    _FakeReceiveClient.pending_items = []
    _FakeReceiveClient.raise_on_receive = 0


def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _make_item() -> tuple[_FakeFrame, _FakeMessage]:
    return (
        _FakeFrame(delivery_id=1, delivery_tag=b"tag-1"),
        _FakeMessage(data=[b'{"hello": "world"}']),
    )


def test_ack_from_dispatch_thread_is_marshalled_to_consumer_thread() -> None:
    client = _client()
    callback_threads: list[str] = []
    acked = threading.Event()

    def cb(payload: Any, headers: dict[str, str], queue_name: str) -> None:
        callback_threads.append(threading.current_thread().name)
        client.ack_message(queue_name, headers)
        acked.set()

    _FakeReceiveClient.pending_items = [_make_item()]
    try:
        client.subscribe_to_queue("q1", "topic1", cb)

        assert acked.wait(timeout=5.0)
        # Wait until the marshalled settlement has been performed and evicted.
        assert _wait_for(lambda: not client._deliveries)

        receive_client = _FakeReceiveClient.instances[0]
        assert len(receive_client.settle_calls) == 1
        _, _, outcome, settle_thread = receive_client.settle_calls[0]
        assert outcome == "accepted"

        # settle_messages must run on the consumer thread that owns the link,
        # never on the dispatch thread that ran the callback.
        assert settle_thread in receive_client.receive_threads
        assert settle_thread.startswith("event-mesh-amqp-consumer")
        assert callback_threads
        assert all(t != settle_thread for t in callback_threads)
        assert all(
            not t.startswith("event-mesh-amqp-consumer") for t in callback_threads
        )
    finally:
        client.close()


def test_nack_from_dispatch_thread_releases_on_consumer_thread() -> None:
    client = _client()
    done = threading.Event()

    def cb(payload: Any, headers: dict[str, str], queue_name: str) -> None:
        client.nack_message(queue_name, headers, requeue=True)
        done.set()

    _FakeReceiveClient.pending_items = [_make_item()]
    try:
        client.subscribe_to_queue("q1", "topic1", cb)
        assert done.wait(timeout=5.0)
        assert _wait_for(lambda: not client._deliveries)

        receive_client = _FakeReceiveClient.instances[0]
        assert len(receive_client.settle_calls) == 1
        _, _, outcome, settle_thread = receive_client.settle_calls[0]
        assert outcome == "released"
        assert settle_thread.startswith("event-mesh-amqp-consumer")
    finally:
        client.close()


def test_raising_callback_is_isolated_and_auto_settles() -> None:
    client = _client()
    invoked = threading.Event()

    def cb(payload: Any, headers: dict[str, str], queue_name: str) -> None:
        invoked.set()
        raise ValueError("callback blew up")

    _FakeReceiveClient.pending_items = [_make_item()]
    try:
        client.subscribe_to_queue("q1", "topic1", cb)
        assert invoked.wait(timeout=5.0)

        # Dispatch must survive the raising callback and the delivery must be
        # auto-settled (error default = released) and evicted.
        assert _wait_for(lambda: not client._deliveries)
        receive_client = _FakeReceiveClient.instances[0]
        assert len(receive_client.settle_calls) == 1
        assert receive_client.settle_calls[0][2] == "released"

        # Runtime is still alive after the exception: a second delivery is still
        # processed and settled.
        _FakeReceiveClient.pending_items = [
            (_FakeFrame(delivery_id=2, delivery_tag=b"tag-2"), _FakeMessage([b"{}"]))
        ]
        assert _wait_for(lambda: len(receive_client.settle_calls) == 2)
    finally:
        client.close()


def test_delivery_that_is_never_acked_does_not_leak() -> None:
    client = _client()
    seen = 0
    seen_lock = threading.Lock()

    def cb(payload: Any, headers: dict[str, str], queue_name: str) -> None:
        nonlocal seen
        with seen_lock:
            seen += 1
        # Deliberately never ack/nack.

    # Feed several messages; none get explicit settlement.
    _FakeReceiveClient.pending_items = [
        (
            _FakeFrame(delivery_id=i, delivery_tag=f"tag-{i}".encode()),
            _FakeMessage([b"{}"]),
        )
        for i in range(6)
    ]
    try:
        client.subscribe_to_queue("q1", "topic1", cb)
        assert _wait_for(lambda: seen == 6)
        # Auto-settle-on-completion bounds _deliveries: it drains to empty even
        # though no callback ever acked.
        assert _wait_for(lambda: not client._deliveries)
        receive_client = _FakeReceiveClient.instances[0]
        assert len(receive_client.settle_calls) == 6
        assert all(call[2] == "accepted" for call in receive_client.settle_calls)
    finally:
        client.close()


def test_credentials_are_per_instance_not_global_env() -> None:
    # MINI-1: exporting one client's auth must not write process-global env or
    # clobber another client/tenant, and must not leak a token-structure
    # diagnostic env var.
    for name in (
        "EVENT_MESH_AMQP_WS_AUTHORIZATION",
        "EVENT_MESH_AMQP_WS_BEARER_TOKEN",
        "EVENT_MESH_AMQP_WS_AUTH_DIAGNOSTIC",
        "EVENT_MESH_MESSAGING_AMQP10WS_URL",
    ):
        os.environ.pop(name, None)
    ws.clear_amqp_ws_context()

    def _mk(token: str) -> EventMeshAMQPBrokerClient:
        client = EventMeshAMQPBrokerClient(
            token_url="",
            messaging_url="wss://h/protocols/amqp10ws",
            management_url="",
            client_id="i",
            client_secret=token,
            auth_mode="bearer",
        )
        client._get_access_token = lambda *, force_refresh=False: token  # type: ignore[assignment]
        return client

    client_a = _mk("tok-AAA")
    client_b = _mk("tok-BBB")
    try:
        client_a._export_amqp_ws_authorization_for_vendor_transport()
        assert ws._event_mesh_ws_authorization_header() == "Bearer tok-AAA"

        # No process-global writes, and specifically no token-structure leak.
        assert os.environ.get("EVENT_MESH_AMQP_WS_AUTHORIZATION") is None
        assert os.environ.get("EVENT_MESH_AMQP_WS_AUTH_DIAGNOSTIC") is None
        assert os.environ.get("EVENT_MESH_MESSAGING_AMQP10WS_URL") is None

        # A different tenant on a different thread does not clobber this one.
        other: dict[str, str] = {}

        def _worker() -> None:
            client_b._export_amqp_ws_authorization_for_vendor_transport()
            other["header"] = ws._event_mesh_ws_authorization_header()

        thread = threading.Thread(target=_worker)
        thread.start()
        thread.join()

        assert other["header"] == "Bearer tok-BBB"
        # This thread's context is unchanged by the other tenant's export.
        assert ws._event_mesh_ws_authorization_header() == "Bearer tok-AAA"
    finally:
        ws.clear_amqp_ws_context()
        client_a.close()
        client_b.close()


def test_reconnect_recreates_receive_client_with_refreshed_token() -> None:
    client = _client()
    # First receive call raises -> loop must close and recreate the client.
    _FakeReceiveClient.raise_on_receive = 1
    try:
        client.subscribe_to_queue("q1", "topic1", lambda p, h, q: None)

        assert _wait_for(lambda: len(_FakeReceiveClient.instances) >= 2)
        first = _FakeReceiveClient.instances[0]
        assert first.closed is True
        # Reconnect refreshed the OAuth token (force_refresh=True recorded).
        assert True in client._token_refresh_calls  # type: ignore[attr-defined]
    finally:
        client.close()
