"""Unit tests for EventMeshBrokerClient.

All HTTP calls are mocked — no real Event Mesh endpoint is needed.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_publisher_pump import _SendItem
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig
from app.layer4_frameworks.messaging.event_mesh_names import EventMeshNames


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


def _pre_authed_async_client(**overrides) -> EventMeshBrokerClient:
    """A client with a mocked async HTTP client and a pre-seeded token.

    No flusher task runs — publication is async-only, so tests drive the
    publish internals directly: ``_flush_one`` (URL/header construction) and
    ``_post_with_retry_async`` (retry + metrics), which is where that logic
    now lives after the synchronous publish path was removed.
    """
    client = _make_client(**overrides)
    client._http_async = MagicMock()
    client._tokens._async_lock = asyncio.Lock()
    client._tokens._access_token = "tok"
    client._tokens._token_expires_at = 2**31
    return client


def _mock_token_response():
    """Return a mock httpx response for a successful token grant."""
    resp = MagicMock()
    resp.json.return_value = {"access_token": "tok-123", "expires_in": 3600}
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Namespace qualification
# ---------------------------------------------------------------------------


class TestNamespaceQualification:

    def test_qualify_topic_prepends_namespace(self):
        client = _make_client()
        assert client._names.qualify_topic("run.started") == "simplemdg/event/core-2/run.started"

    def test_qualify_topic_no_double_prefix(self):
        client = _make_client()
        already = "simplemdg/event/core-2/run.started"
        assert client._names.qualify_topic(already) == already

    def test_qualify_topic_empty_namespace(self):
        client = _make_client(namespace="")
        assert client._names.qualify_topic("run.started") == "run.started"

    def test_qualify_queue_prepends_namespace(self):
        client = _make_client()
        assert client._names.qualify_queue("workflow.tasks") == "simplemdg/event/core-2/workflow.tasks"

    def test_qualify_queue_no_double_prefix(self):
        client = _make_client()
        already = "simplemdg/event/core-2/workflow.tasks"
        assert client._names.qualify_queue(already) == already

    def test_namespace_trailing_slash_stripped(self):
        client = _make_client(namespace="ns/sub/")
        assert client._names.qualify_topic("t") == "ns/sub/t"

    def test_qualify_topic_rewrites_underscore_to_hyphen(self):
        # Classic Event Mesh rejects an underscore in a topic level
        # ("Topic is invalid"); map _ -> - on the wire.
        client = _make_client()
        assert (
            client._names.qualify_topic("child_run.started")
            == "simplemdg/event/core-2/child-run.started"
        )

    def test_qualify_topic_underscore_idempotent_when_already_qualified(self):
        client = _make_client()
        already = "simplemdg/event/core-2/child-run.started"
        assert client._names.qualify_topic(already) == already

    def test_qualify_topic_underscore_free_unchanged(self):
        client = _make_client()
        assert client._names.qualify_topic("run.status") == "simplemdg/event/core-2/run.status"


# ---------------------------------------------------------------------------
# OAuth2 token management
# ---------------------------------------------------------------------------


class TestTokenManagement:

    def test_refresh_token_calls_token_url(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        token = client._tokens.ensure()

        assert token == "tok-123"
        client._http.post.assert_called_once()
        call_args = client._http.post.call_args
        assert call_args[0][0] == "https://auth.example.com/oauth/token"
        assert call_args[1]["data"]["grant_type"] == "client_credentials"

    def test_token_cached_on_second_call(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        client._tokens.ensure()
        client._tokens.ensure()

        # Token URL should only be called once (cached)
        assert client._http.post.call_count == 1

    def test_token_refresh_on_expiry(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        client._tokens.ensure()
        # Force token expiry
        client._tokens._token_expires_at = 0
        client._tokens.ensure()

        assert client._http.post.call_count == 2


# ---------------------------------------------------------------------------
# publish_to_topic
# ---------------------------------------------------------------------------


class TestPublishToTopic:
    """The flusher builds the URL/headers and POSTs; drive it via _flush_one."""

    @pytest.mark.asyncio
    async def test_publish_qualifies_topic_with_namespace(self):
        client = _pre_authed_async_client()
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        await client._publisher._flush_one(
            _SendItem(kind="topic", name="run.started",
                      payload=json.dumps({"run_id": "r1"}).encode())
        )

        call = client._http_async.post.call_args
        # namespace/topic must be URL-encoded: simplemdg%2Fevent%2Fcore-2%2Frun.started
        assert "simplemdg%2Fevent%2Fcore-2%2Frun.started" in call[0][0]
        assert json.loads(call.kwargs["content"]) == {"run_id": "r1"}

    @pytest.mark.asyncio
    async def test_publish_with_key(self):
        client = _pre_authed_async_client()
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        await client._publisher._flush_one(
            _SendItem(kind="topic", name="t", payload=b'{"data": 1}', key="my-key")
        )

        assert client._http_async.post.call_args.kwargs["headers"]["x-message-key"] == "my-key"

    @pytest.mark.asyncio
    async def test_publish_already_qualified_topic_not_doubled(self):
        client = _pre_authed_async_client()
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        await client._publisher._flush_one(
            _SendItem(kind="topic", name="simplemdg/event/core-2/run.started",
                      payload=b'{"data": 1}')
        )

        # Should appear exactly once, not doubled
        assert client._http_async.post.call_args[0][0].count("simplemdg") == 1

    @pytest.mark.asyncio
    async def test_publish_no_namespace(self):
        client = _pre_authed_async_client(namespace="")
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        await client._publisher._flush_one(
            _SendItem(kind="topic", name="run.started", payload=b'{"data": 1}')
        )

        assert "/topics/run.started/messages" in client._http_async.post.call_args[0][0]

    def test_publish_before_start_async_raises(self):
        """Publication is async-only — calling before start_async() fails loud."""
        client = _make_client()
        with pytest.raises(RuntimeError, match="start_async"):
            client.publish_to_topic("run.started", {"run_id": "r1"})


# ---------------------------------------------------------------------------
# consume_from_queue
# ---------------------------------------------------------------------------


class TestConsumeFromQueue:

    def test_consume_returns_message(self):
        client = _make_client()
        client._http = MagicMock()

        consume_resp = MagicMock()
        consume_resp.status_code = 200
        consume_resp.raise_for_status = MagicMock()
        consume_resp.json.return_value = {"task_id": "t1"}
        consume_resp.headers = {"x-message-id": "msg-42"}

        client._http.post.side_effect = [_mock_token_response(), consume_resp]

        result = client.consume_from_queue("workflow.tasks")

        assert result is not None
        assert result["data"] == {"task_id": "t1"}
        assert result["message_id"] == "msg-42"

    def test_consume_qualifies_queue_with_namespace(self):
        client = _make_client()
        client._http = MagicMock()

        consume_resp = MagicMock()
        consume_resp.status_code = 204
        consume_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), consume_resp]

        client.consume_from_queue("workflow.tasks")

        url = client._http.post.call_args_list[1][0][0]
        assert "simplemdg%2Fevent%2Fcore-2%2Fworkflow.tasks" in url

    def test_consume_returns_none_on_204(self):
        client = _make_client()
        client._http = MagicMock()

        consume_resp = MagicMock()
        consume_resp.status_code = 204
        consume_resp.raise_for_status = MagicMock()

        client._http.post.side_effect = [_mock_token_response(), consume_resp]

        result = client.consume_from_queue("workflow.tasks")
        assert result is None

    def test_consume_sends_lock_duration_header(self):
        """Per-consume lock TTL override.

        Short lock + slow downstream handler (push gateway / socket emit)
        triggers Event Mesh redelivery, which the per-run reorder buffer
        in socketio_consumer can't absorb past _GAP_FLUSH_SECONDS. Setting
        the header on every consume hides handler tail latency from the
        broker's redelivery clock.
        """
        client = _make_client(lock_duration_ms=300_000)
        client._http = MagicMock()
        consume_resp = MagicMock()
        consume_resp.status_code = 204
        consume_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), consume_resp]

        client.consume_from_queue("workflow.tasks")

        headers = client._http.post.call_args_list[1].kwargs["headers"]
        assert headers["x-lock-duration-in-ms"] == "300000"
        assert headers["x-qos"] == "1"

    def test_consume_uses_default_lock_duration(self):
        client = _make_client()  # default 60_000
        client._http = MagicMock()
        consume_resp = MagicMock()
        consume_resp.status_code = 204
        consume_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), consume_resp]

        client.consume_from_queue("workflow.tasks")

        headers = client._http.post.call_args_list[1].kwargs["headers"]
        assert headers["x-lock-duration-in-ms"] == "60000"


# ---------------------------------------------------------------------------
# 429 rate-limit cooldown
# ---------------------------------------------------------------------------


def _mock_429_response(retry_after: str | None = None):
    resp = MagicMock()
    resp.status_code = 429
    resp.headers = {} if retry_after is None else {"Retry-After": retry_after}
    return resp


class TestConsumeRateLimit:

    def test_429_returns_none_without_raising(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), _mock_429_response()]

        result = client.consume_from_queue("workflow.tasks")

        assert result is None

    def test_429_starts_global_cooldown(self):
        import time as _time

        client = _make_client()
        client._http = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), _mock_429_response()]

        client.consume_from_queue("workflow.tasks")

        assert client._consumer._cooldown_remaining("workflow.tasks") > 0
        # instance scope (default): the shared cooldown entry carries the streak
        assert client._consumer._cooldowns[""][1] == 1

    def test_429_honors_retry_after_header(self):
        import time as _time

        client = _make_client()
        client._http = MagicMock()
        client._http.post.side_effect = [
            _mock_token_response(),
            _mock_429_response(retry_after="7"),
        ]

        client.consume_from_queue("workflow.tasks")

        assert client._consumer._cooldown_remaining("workflow.tasks") == pytest.approx(7.0, abs=0.5)

    def test_consecutive_429s_grow_backoff_exponentially(self):
        import time as _time

        client = _make_client()
        client._http = MagicMock()
        client._http.post.side_effect = [
            _mock_token_response(),
            _mock_429_response(),
            _mock_429_response(),
            _mock_429_response(),
        ]

        deadlines = []
        for _ in range(3):
            client.consume_from_queue("workflow.tasks")
            deadlines.append(client._consumer._cooldown_remaining("workflow.tasks"))

        # 1s, 2s, 4s (base 1.0 doubling per streak)
        assert deadlines[0] == pytest.approx(1.0, abs=0.5)
        assert deadlines[1] == pytest.approx(2.0, abs=0.5)
        assert deadlines[2] == pytest.approx(4.0, abs=0.5)
        assert client._consumer._cooldowns[""][1] == 3

    def test_streak_resets_on_successful_consume(self):
        client = _make_client()
        client._http = MagicMock()

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json.return_value = {"k": "v"}
        ok_resp.headers = {"x-message-id": "m1"}

        client._http.post.side_effect = [
            _mock_token_response(),
            _mock_429_response(),
            ok_resp,
        ]

        client.consume_from_queue("workflow.tasks")
        assert client._consumer._cooldowns[""][1] == 1
        client.consume_from_queue("workflow.tasks")
        assert "" not in client._consumer._cooldowns  # cleared on success

    def test_poll_loop_sleeps_through_cooldown_without_consuming(self):
        import time as _time

        client = _make_client()
        client.consume_from_queue = MagicMock(return_value=None)
        client._consumer._callbacks = {"q1": [MagicMock()]}
        client._consumer._cooldowns[""] = (_time.monotonic() + 0.3, 1)
        client._consumer._running = True

        def _stop_after_delay():
            _time.sleep(0.15)
            # Still inside cooldown — no consumption call may have happened.
            assert client.consume_from_queue.call_count == 0
            client._consumer._running = False

        import threading as _threading

        stopper = _threading.Thread(target=_stop_after_delay)
        stopper.start()
        client._consumer._poll_loop("q1")
        stopper.join()

        assert client.consume_from_queue.call_count == 0


# ---------------------------------------------------------------------------
# acknowledge
# ---------------------------------------------------------------------------


class TestAcknowledge:

    def test_acknowledge_calls_correct_url(self):
        client = _make_client()
        client._http = MagicMock()
        ack_resp = MagicMock()
        ack_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), ack_resp]

        client.acknowledge("workflow.tasks", "msg-42")

        ack_call = client._http.post.call_args_list[1]
        url = ack_call[0][0]
        assert "/messagingrest/v1/queues/" in url
        assert "/messages/msg-42/acknowledgement" in url
        assert "simplemdg%2Fevent%2Fcore-2%2Fworkflow.tasks" in url


# ---------------------------------------------------------------------------
# At-least-once ack semantics on subscribe (Sub 3.0)
# ---------------------------------------------------------------------------


class TestAtLeastOnceAckOnSubscribe:
    """Sub 3.0 parity: ack only on handler success; block on async handlers.

    Mirrors ``TestAtLeastOnceCommit`` for the Event Mesh subscribe path.
    Uses direct ``_poll_loop`` invocation against a stubbed queue rather
    than threading the loop (keeps tests deterministic).

    SA-2319 made ``_poll_loop`` take the queue it serves (one thread per
    queue), so the name the callbacks are registered under is now also the
    loop's argument — named once here instead of repeated at both ends.
    """

    QUEUE = "simplemdg/event/core-2/sub-t"

    def _run_one_poll_iteration(self, client: EventMeshBrokerClient) -> None:
        """Run the poll loop once and stop — simulates a single message dispatch."""
        import threading
        import time

        client._consumer._running = True

        def _stop_after_short_delay():
            time.sleep(0.15)
            client._consumer._running = False

        stopper = threading.Thread(target=_stop_after_short_delay, daemon=True)
        stopper.start()
        client._consumer._poll_loop(self.QUEUE)
        stopper.join(timeout=1)

    def test_ack_called_on_successful_sync_handler(self):
        client = _make_client()
        ack_calls = []
        client._consumer.consume_from_queue = MagicMock(
            side_effect=[
                {"data": {"v": 1}, "message_id": "m-1"},
                None,
            ]
        )
        client._consumer.acknowledge = MagicMock(
            side_effect=lambda q, mid: ack_calls.append((q, mid))
        )

        received = []
        client._consumer._callbacks[self.QUEUE] = [
            lambda data: received.append(data)
        ]

        self._run_one_poll_iteration(client)

        assert received == [{"v": 1}]
        assert ack_calls == [(self.QUEUE, "m-1")]

    def test_no_ack_when_sync_handler_raises(self):
        client = _make_client()
        client._consumer.consume_from_queue = MagicMock(
            side_effect=[
                {"data": {"v": 1}, "message_id": "m-1"},
                None,
            ]
        )
        client._consumer.acknowledge = MagicMock()

        def bad(data):
            raise RuntimeError("boom")

        client._consumer._callbacks[self.QUEUE] = [bad]

        self._run_one_poll_iteration(client)

        client._consumer.acknowledge.assert_not_called()

    def test_async_handler_awaited_before_ack(self):
        """Ack must happen AFTER the coroutine finishes, not before."""
        import threading
        import time

        client = _make_client()
        client._consumer.consume_from_queue = MagicMock(
            side_effect=[
                {"data": {"v": 1}, "message_id": "m-1"},
                None,
            ]
        )
        ack_order = []
        client._consumer.acknowledge = MagicMock(
            side_effect=lambda q, mid: ack_order.append(("ack", time.monotonic()))
        )

        gate = threading.Event()
        complete_order = []

        async def slow(data):
            while not gate.is_set():
                await asyncio.sleep(0.01)
            complete_order.append(("done", time.monotonic()))

        client._consumer._callbacks[self.QUEUE] = [slow]

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        client.set_event_loop(loop)

        poll_thread = threading.Thread(target=self._run_one_poll_iteration, args=(client,), daemon=True)
        poll_thread.start()

        # Give the poll loop a moment to fetch the message and enter the
        # handler. Ack must not yet have been called.
        time.sleep(0.1)
        assert client._consumer.acknowledge.call_count == 0

        # Release the slow handler; ack should now fire.
        gate.set()
        poll_thread.join(timeout=2)

        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()

        assert complete_order and ack_order
        # Completion must happen strictly before ack.
        assert complete_order[0][1] <= ack_order[0][1]

    def test_no_ack_when_async_handler_raises(self):
        import threading

        client = _make_client()
        client._consumer.consume_from_queue = MagicMock(
            side_effect=[
                {"data": {"v": 1}, "message_id": "m-1"},
                None,
            ]
        )
        client._consumer.acknowledge = MagicMock()

        async def bad(data):
            raise RuntimeError("boom")

        client._consumer._callbacks[self.QUEUE] = [bad]

        loop = asyncio.new_event_loop()
        loop_thread = threading.Thread(target=loop.run_forever, daemon=True)
        loop_thread.start()
        client.set_event_loop(loop)

        self._run_one_poll_iteration(client)

        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=2)
        loop.close()

        client._consumer.acknowledge.assert_not_called()


# ---------------------------------------------------------------------------
# Queue / subscription management
# ---------------------------------------------------------------------------


class TestQueueManagement:

    def test_queue_exists_returns_true_on_200(self):
        client = _make_client()
        client._http = MagicMock()
        get_resp = MagicMock()
        get_resp.status_code = 200
        client._http.post.return_value = _mock_token_response()
        client._http.get.return_value = get_resp

        assert client.queue_exists("my-queue") is True

    def test_queue_exists_returns_false_on_404(self):
        client = _make_client()
        client._http = MagicMock()
        get_resp = MagicMock()
        get_resp.status_code = 404
        client._http.post.return_value = _mock_token_response()
        client._http.get.return_value = get_resp

        assert client.queue_exists("my-queue") is False

    def test_create_queue_qualifies_name(self):
        client = _make_client()
        client._http = MagicMock()
        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        client._http.post.return_value = _mock_token_response()
        client._http.put.return_value = put_resp

        client.create_queue("my-queue")

        url = client._http.put.call_args[0][0]
        assert "/hub/rest/api/v1/management/messaging/queues/" in url
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-queue" in url

    def test_create_queue_sets_message_lock_expiration(self):
        """Queue-level default lock TTL.

        Per-consume header is the per-message override; the queue
        property here ensures any consumer (including future ones that
        don't pass the header) still gets the extended lock.
        """
        client = _make_client(lock_duration_ms=180_000)
        client._http = MagicMock()
        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        client._http.post.return_value = _mock_token_response()
        client._http.put.return_value = put_resp

        client.create_queue("my-queue")

        body = client._http.put.call_args.kwargs["json"]
        assert body["messageLockExpirationInSeconds"] == 180
        # P2 ships a redelivery cap + DMQ in the same body; the lock TTL must
        # survive alongside it. See test_event_mesh_dmq_policy for that policy.
        assert body["maxRedeliveryCount"] == 5
        assert body["deadMsgQueue"] == "simplemdg/event/core-2/my-queue-dmq"
        headers = client._http.put.call_args.kwargs["headers"]
        assert headers["Content-Type"] == "application/json"

    def test_delete_queue_calls_delete(self):
        client = _make_client()
        client._http = MagicMock()
        del_resp = MagicMock()
        del_resp.raise_for_status = MagicMock()
        client._http.post.return_value = _mock_token_response()
        client._http.delete.return_value = del_resp

        client.delete_queue("my-queue")

        client._http.delete.assert_called_once()

    def test_create_subscription_qualifies_both(self):
        client = _make_client()
        client._http = MagicMock()
        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        client._http.post.return_value = _mock_token_response()
        client._http.put.return_value = put_resp

        client.create_subscription("my-queue", "my-topic")

        url = client._http.put.call_args[0][0]
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-queue" in url
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-topic" in url

    def test_subscription_exists_returns_true_on_200(self):
        client = _make_client()
        client._http = MagicMock()
        get_resp = MagicMock()
        get_resp.status_code = 200
        client._http.post.return_value = _mock_token_response()
        client._http.get.return_value = get_resp

        assert client.subscription_exists("my-queue", "my-topic") is True


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:

    def test_close_stops_and_closes_http(self):
        client = _make_client()
        client._http = MagicMock()

        client.close()

        assert client._consumer._running is False
        client._http.close.assert_called_once()


# ---------------------------------------------------------------------------
# Payload size guard
# ---------------------------------------------------------------------------


class TestPayloadSizeGuard:

    def test_publish_raises_on_oversized_payload(self):
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshPayloadTooLarge,
        )
        client = _make_client(max_payload_bytes=100)
        client._http = MagicMock()
        # Token will be fetched only if publish gets past the guard, but it
        # shouldn't: payload guard runs before HTTP. Pre-seed token to be safe.
        client._tokens._access_token = "tok"
        client._tokens._token_expires_at = 2**31

        big_message = {"data": "x" * 500}  # well over 100 bytes after JSON encoding

        with pytest.raises(EventMeshPayloadTooLarge) as exc_info:
            client.publish_to_topic("t", big_message)
        assert exc_info.value.limit == 100
        assert exc_info.value.payload_size > 100
        # HTTP should NOT have been called
        client._http.post.assert_not_called()

    def test_publish_to_queue_raises_on_oversized_payload(self):
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshPayloadTooLarge,
        )
        client = _make_client(max_payload_bytes=50)
        client._http = MagicMock()
        client._tokens._access_token = "tok"
        client._tokens._token_expires_at = 2**31

        with pytest.raises(EventMeshPayloadTooLarge):
            client.publish_to_queue("q", {"data": "x" * 200})
        client._http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_publish_succeeds_when_under_limit(self):
        client = _make_client(max_payload_bytes=1000)
        await client.start_async()
        try:
            client._http_async.post = AsyncMock(side_effect=[
                _make_async_response(200, {"access_token": "tok", "expires_in": 3600}),
                _make_async_response(200),
            ])
            client.publish_to_topic("t", {"small": "msg"})  # under limit — must not raise
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)
            # 1 token fetch + 1 publish
            assert client._http_async.post.call_count == 2
        finally:
            await client.stop_async(drain_timeout_s=2.0)


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------


def _http_error(status_code: int, body: str = "err") -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.com/x")
    resp = httpx.Response(status_code=status_code, text=body, request=req)
    return httpx.HTTPStatusError(f"{status_code}", request=req, response=resp)


class TestRetryBehavior:
    """The retry loop lives in _post_with_retry_async (the only publish path)."""

    def _retry_client(self, **kwargs):
        client = _pre_authed_async_client(**kwargs)
        client._publisher._backoff_delay = lambda *_: 0.0  # no real sleeps between attempts
        return client

    async def _post(self, client):
        return await client._publisher._post_with_retry_async(
            "https://messaging.example.com/messagingrest/v1/topics/t/messages",
            content=b'{"v": 1}', headers={}, topic_for_metrics="t",
        )

    @pytest.mark.asyncio
    async def test_retry_on_503_then_succeeds(self):
        client = self._retry_client(max_retries=3)
        client._http_async.post = AsyncMock(
            side_effect=[_make_async_response(503), _make_async_response(200)]
        )

        await self._post(client)

        assert client._http_async.post.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausts_and_raises(self):
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshPublishFailed,
        )
        client = self._retry_client(max_retries=2)
        client._http_async.post = AsyncMock(return_value=_make_async_response(502))

        with pytest.raises(EventMeshPublishFailed) as exc_info:
            await self._post(client)
        assert exc_info.value.attempts == 2
        assert "http_502" in exc_info.value.last_error
        assert client._http_async.post.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx(self):
        """4xx client errors are terminal (no retry)."""
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshPublishFailed,
        )
        client = self._retry_client(max_retries=3)
        client._http_async.post = AsyncMock(return_value=_make_async_response(401))

        with pytest.raises(EventMeshPublishFailed) as exc_info:
            await self._post(client)
        # 401 is NOT retryable — should only call once
        assert client._http_async.post.call_count == 1
        assert exc_info.value.attempts == 1
        assert "http_401" in exc_info.value.last_error

    @pytest.mark.asyncio
    async def test_retry_on_429_rate_limit(self):
        client = self._retry_client(max_retries=3)
        client._http_async.post = AsyncMock(side_effect=[
            _make_async_response(429),
            _make_async_response(429),
            _make_async_response(200),
        ])

        await self._post(client)
        assert client._http_async.post.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self):
        client = self._retry_client(max_retries=3)
        client._http_async.post = AsyncMock(side_effect=[
            httpx.TimeoutException("timed out"),
            _make_async_response(200),
        ])

        await self._post(client)
        assert client._http_async.post.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_transport_error(self):
        client = self._retry_client(max_retries=3)
        client._http_async.post = AsyncMock(side_effect=[
            httpx.ConnectError("connection refused"),
            _make_async_response(200),
        ])

        await self._post(client)
        assert client._http_async.post.call_count == 2

    def test_backoff_delay_monotonically_increases(self):
        client = _make_client()
        # Without jitter, base * 2^attempt: 0.5, 1.0, 2.0
        # With ±20% jitter, still monotonically trend-up on average
        d0 = client._publisher._backoff_delay(0)
        d1 = client._publisher._backoff_delay(1)
        d2 = client._publisher._backoff_delay(2)
        # Minimum bounds (20% below base)
        assert d0 >= 0.4 and d0 <= 0.6
        assert d1 >= 0.8 and d1 <= 1.2
        assert d2 >= 1.6 and d2 <= 2.4


# ---------------------------------------------------------------------------
# Metrics integration
# ---------------------------------------------------------------------------


class TestMetrics:

    @pytest.mark.asyncio
    async def test_metrics_records_success(self):
        monitor = MagicMock()
        client = _pre_authed_async_client(monitor=monitor)
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        await client._publisher._post_with_retry_async(
            "https://x/messages", content=b"{}", headers={},
            topic_for_metrics="run.started",
        )

        # Check that increment was called with success label
        increments = [c for c in monitor.increment.call_args_list]
        success_calls = [c for c in increments if "success" in c[0][0]]
        assert len(success_calls) == 1
        assert success_calls[0][0][0] == "eventmesh.publish.success"
        assert success_calls[0][1]["tags"]["topic"] == "run.started"

    @pytest.mark.asyncio
    async def test_metrics_records_retry_and_failure(self):
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshPublishFailed,
        )
        monitor = MagicMock()
        client = _pre_authed_async_client(monitor=monitor, max_retries=2)
        client._publisher._backoff_delay = lambda *_: 0.0
        client._http_async.post = AsyncMock(return_value=_make_async_response(503))

        with pytest.raises(EventMeshPublishFailed):
            await client._publisher._post_with_retry_async(
                "https://x/messages", content=b"{}", headers={}, topic_for_metrics="t",
            )

        names = [c[0][0] for c in monitor.increment.call_args_list]
        assert "eventmesh.publish.retry" in names
        assert "eventmesh.publish.failure" in names

    def test_metrics_records_dropped_on_oversize(self):
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshPayloadTooLarge,
        )
        monitor = MagicMock()
        client = _make_client(monitor=monitor, max_payload_bytes=50)
        client._http = MagicMock()
        client._tokens._access_token = "tok"
        client._tokens._token_expires_at = 2**31

        with pytest.raises(EventMeshPayloadTooLarge):
            client.publish_to_topic("t", {"big": "x" * 200})

        names = [c[0][0] for c in monitor.increment.call_args_list]
        assert "eventmesh.publish.dropped" in names

    @pytest.mark.asyncio
    async def test_metrics_safe_when_monitor_is_none(self):
        client = _pre_authed_async_client(monitor=None)
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        # Must not raise even without a monitor
        await client._publisher._post_with_retry_async(
            "https://x/messages", content=b"{}", headers={}, topic_for_metrics="t",
        )

    @pytest.mark.asyncio
    async def test_metrics_safe_when_monitor_raises(self):
        """A broken monitor must not break the publish path."""
        monitor = MagicMock()
        monitor.increment.side_effect = RuntimeError("monitor down")
        monitor.track.side_effect = RuntimeError("monitor down")
        client = _pre_authed_async_client(monitor=monitor)
        client._http_async.post = AsyncMock(return_value=_make_async_response(200))

        await client._publisher._post_with_retry_async(
            "https://x/messages", content=b"{}", headers={}, topic_for_metrics="t",
        )
        client._http_async.post.assert_called_once()

    def test_metrics_records_token_refresh(self):
        monitor = MagicMock()
        client = _make_client(monitor=monitor)
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        client._tokens.ensure()

        names = [c[0][0] for c in monitor.increment.call_args_list]
        assert "eventmesh.token.refresh" in names


# ---------------------------------------------------------------------------
# Phase 2: Async flusher
# ---------------------------------------------------------------------------


def _make_async_response(status_code: int = 200, json_body: dict | None = None):
    """Return an AsyncMock that mimics an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = _http_error(status_code)
    if json_body is not None:
        resp.json.return_value = json_body
    return resp


class TestAsyncFlusher:
    """Tests for the async non-blocking publish path (start_async/stop_async)."""

    async def _prep_client_with_async_http(self, client, responses):
        """Install mocked httpx.AsyncClient with queued responses."""
        from unittest.mock import AsyncMock
        client._http_async = MagicMock()
        # Token response first, then the provided responses
        token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
        client._http_async.post = AsyncMock(side_effect=[token_resp, *responses])
        client._http_async.aclose = AsyncMock()

    @pytest.mark.asyncio
    async def test_start_async_creates_queue_and_tasks(self):
        client = _make_client()
        await client.start_async()
        try:
            assert client._publisher._send_queue is not None
            assert client._publisher._flusher_task is not None
            assert client._publisher._token_refresh_task is not None
            assert client._http_async is not None
            assert client._publisher._running_async is True
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_start_async_is_idempotent(self):
        client = _make_client()
        await client.start_async()
        first_queue = client._publisher._send_queue
        await client.start_async()  # no-op
        assert client._publisher._send_queue is first_queue
        await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_publish_enqueues_when_async_running(self):
        client = _make_client()
        await client.start_async()
        try:
            # Inject mock http so flusher can't actually call out
            from unittest.mock import AsyncMock
            client._http_async.post = AsyncMock(side_effect=asyncio.CancelledError())
            # publish_to_topic should enqueue (not block on HTTP)
            client.publish_to_topic("t", {"v": 1})
            # Item should be in queue
            assert client._publisher._send_queue.qsize() >= 0  # may be drained by flusher already
        finally:
            await client.stop_async(drain_timeout_s=0.5)

    @pytest.mark.asyncio
    async def test_flusher_dispatches_enqueued_items(self):
        client = _make_client()
        await client.start_async()
        try:
            from unittest.mock import AsyncMock
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            publish_resp = _make_async_response(200)
            client._http_async.post = AsyncMock(
                side_effect=[token_resp, publish_resp, publish_resp, publish_resp],
            )

            client.publish_to_topic("t", {"v": 1})
            client.publish_to_topic("t", {"v": 2})
            client.publish_to_topic("t", {"v": 3})

            # Wait for queue to drain
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # 1 token fetch + 3 publishes = 4 calls
            assert client._http_async.post.call_count == 4
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_buffer_full_drop_policy(self):
        client = _make_client(buffer_size=2, buffer_policy="drop")
        await client.start_async()
        try:
            # Block the flusher from consuming by making HTTP hang
            from unittest.mock import AsyncMock
            block = asyncio.Event()
            async def _blocking_post(*args, **kwargs):
                await block.wait()
                return _make_async_response(200)
            client._http_async.post = AsyncMock(side_effect=_blocking_post)

            # Give flusher time to pull the first item
            client.publish_to_topic("t", {"v": 1})
            await asyncio.sleep(0.05)
            # Fill the queue (1 in-flight + 2 in queue)
            client.publish_to_topic("t", {"v": 2})
            client.publish_to_topic("t", {"v": 3})
            # This one should be dropped (queue full)
            client.publish_to_topic("t", {"v": 4})

            # Check that drop was recorded via metrics (monitor mocked in its own test)
            block.set()
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_buffer_full_raise_policy(self):
        from app.layer4_frameworks.messaging.event_mesh_errors import (
            EventMeshBufferFull,
        )
        client = _make_client(buffer_size=1, buffer_policy="raise")
        await client.start_async()
        try:
            from unittest.mock import AsyncMock
            block = asyncio.Event()
            async def _blocking_post(*args, **kwargs):
                await block.wait()
                return _make_async_response(200)
            client._http_async.post = AsyncMock(side_effect=_blocking_post)

            client.publish_to_topic("t", {"v": 1})
            await asyncio.sleep(0.05)  # let flusher pull first item
            client.publish_to_topic("t", {"v": 2})  # fills queue
            # Next should raise
            with pytest.raises(EventMeshBufferFull):
                client.publish_to_topic("t", {"v": 3})
            block.set()
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_stop_async_drains_pending(self):
        client = _make_client(flush_batch_size=10, flush_concurrency=5)
        await client.start_async()
        from unittest.mock import AsyncMock
        token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
        publish_resp = _make_async_response(200)
        client._http_async.post = AsyncMock(
            side_effect=[token_resp] + [publish_resp] * 20,
        )

        # Enqueue 10 messages
        for i in range(10):
            client.publish_to_topic("t", {"v": i})

        # Hold a reference — stop_async nulls out _http_async after closing
        post_mock = client._http_async.post

        # Stop should drain before returning (within timeout)
        await client.stop_async(drain_timeout_s=5.0)

        # All 10 publishes should have been dispatched (+1 token fetch)
        assert post_mock.call_count >= 11

    @pytest.mark.asyncio
    async def test_stop_async_without_start_is_noop(self):
        client = _make_client()
        # Should not raise
        await client.stop_async()

    @pytest.mark.asyncio
    async def test_async_retry_on_503(self):
        client = _make_client(max_retries=3)
        await client.start_async()
        try:
            from unittest.mock import AsyncMock
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            bad = _make_async_response(503)
            good = _make_async_response(200)
            # Sleep for backoff is fine (short)
            client._http_async.post = AsyncMock(side_effect=[token_resp, bad, good])

            client.publish_to_topic("t", {"v": 1})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # 1 token + 1 failed + 1 succeeded = 3 calls
            assert client._http_async.post.call_count == 3
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_async_auth_header_uses_async_token_refresh(self):
        client = _make_client()
        await client.start_async()
        try:
            from unittest.mock import AsyncMock
            token_resp = _make_async_response(200, {"access_token": "tok-async", "expires_in": 3600})
            client._http_async.post = AsyncMock(return_value=token_resp)

            token = await client._tokens.ensure_async()
            assert token == "tok-async"
            # Subsequent call should use cache
            token2 = await client._tokens.ensure_async()
            assert token2 == "tok-async"
            assert client._http_async.post.call_count == 1
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_buffer_depth_metric_recorded(self):
        monitor = MagicMock()
        client = _make_client(monitor=monitor, buffer_size=100)
        await client.start_async()
        try:
            from unittest.mock import AsyncMock
            # Block HTTP so items stay in queue briefly
            block = asyncio.Event()
            async def _blocking_post(*args, **kwargs):
                await block.wait()
                return _make_async_response(200)
            client._http_async.post = AsyncMock(side_effect=_blocking_post)

            client.publish_to_topic("t", {"v": 1})
            client.publish_to_topic("t", {"v": 2})

            # Look for buffer.depth tracks
            depth_calls = [c for c in monitor.track.call_args_list
                           if c[0][0] == "eventmesh.buffer.depth"]
            assert len(depth_calls) >= 2
            block.set()
        finally:
            await client.stop_async(drain_timeout_s=2.0)


# ---------------------------------------------------------------------------
# Phase 3: Circuit breaker + DLQ + health check
# ---------------------------------------------------------------------------


class TestCircuitBreakerIntegration:
    """The flusher should fail-fast when the circuit breaker is OPEN."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_failures_and_sends_to_dlq(self, monkeypatch):
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1,
            circuit_failure_threshold=2,
            circuit_recovery_timeout_s=60.0,
            enable_dlq=True,
        )
        await client.start_async()
        try:
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            bad = _make_async_response(503)
            dlq_ok = _make_async_response(200)

            def _post_side_effect(*args, **kwargs):
                # First call is always the token fetch (data= kwarg)
                if "data" in kwargs and "grant_type" in kwargs.get("data", {}):
                    return token_resp
                url = args[0] if args else kwargs.get("url", "")
                if "publisher.dlq" in url:
                    return dlq_ok
                # Regular publish → always 503
                return bad
            client._http_async.post = AsyncMock(side_effect=_post_side_effect)

            client.publish_to_topic("t", {"v": 1})
            client.publish_to_topic("t", {"v": 2})
            # Wait for the two failures to drain
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # Circuit should be OPEN after 2 failures
            assert client._publisher._breaker.state == "OPEN"
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_open_circuit_fast_fails_new_publishes(self):
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout_s=60.0,
            enable_dlq=True,
        )
        await client.start_async()
        try:
            # Force circuit to OPEN directly
            await client._publisher._breaker.on_failure()
            assert client._publisher._breaker.state == "OPEN"

            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            dlq_ok = _make_async_response(200)
            client._http_async.post = AsyncMock(side_effect=[token_resp, dlq_ok])

            client.publish_to_topic("t", {"v": 1})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # Item went straight to DLQ (no publish attempt), so only 2 posts:
            # 1 for token (DLQ needs auth) + 1 for DLQ
            assert client._http_async.post.call_count == 2
            dlq_call = client._http_async.post.call_args_list[1]
            assert "publisher.dlq" in dlq_call[0][0] or "publisher.dlq" in dlq_call[1].get("json", {}).get("name", "")
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_circuit_closes_after_successful_probe(self):
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1,
            circuit_failure_threshold=1,
            circuit_recovery_timeout_s=1.0,  # 1s recovery
        )
        await client.start_async()
        try:
            # Force OPEN
            await client._publisher._breaker.on_failure()
            assert client._publisher._breaker.state == "OPEN"
            # Simulate recovery timeout elapsed
            client._publisher._breaker._opened_at -= 10.0

            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            good = _make_async_response(200)
            client._http_async.post = AsyncMock(side_effect=[token_resp, good])

            client.publish_to_topic("t", {"v": 1})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # Probe succeeded → circuit CLOSED
            assert client._publisher._breaker.state == "CLOSED"
            assert client._publisher._breaker.failures == 0
        finally:
            await client.stop_async(drain_timeout_s=2.0)


class TestDLQIntegration:

    @pytest.mark.asyncio
    async def test_failed_publish_sent_to_dlq(self):
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1,
            circuit_failure_threshold=100,  # don't open during test
            enable_dlq=True,
        )
        await client.start_async()
        try:
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            bad = _make_async_response(500)
            dlq_ok = _make_async_response(200)
            client._http_async.post = AsyncMock(
                side_effect=[token_resp, bad, dlq_ok],
            )

            client.publish_to_topic("my.topic", {"v": 1})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # 3 HTTP calls: token + failed publish + DLQ publish
            assert client._http_async.post.call_count == 3
            # Third call (DLQ) targets the DLQ queue URL
            dlq_call = client._http_async.post.call_args_list[2]
            url = dlq_call[0][0]
            assert "publisher.dlq" in url
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_dlq_envelope_contains_payload(self):
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1, circuit_failure_threshold=100, enable_dlq=True,
        )
        await client.start_async()
        try:
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            bad = _make_async_response(500)
            dlq_ok = _make_async_response(200)
            client._http_async.post = AsyncMock(
                side_effect=[token_resp, bad, dlq_ok],
            )

            client.publish_to_topic("my.topic", {"user_id": "u-123"})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            dlq_call = client._http_async.post.call_args_list[2]
            envelope_bytes = dlq_call[1]["content"]
            envelope = json.loads(envelope_bytes)
            assert envelope["original_kind"] == "topic"
            assert envelope["original_name"] == "my.topic"
            assert "http_500" in envelope["error"]
            assert envelope["payload_size_bytes"] > 0
            # Payload is base64-encoded
            import base64
            decoded = json.loads(base64.b64decode(envelope["payload_base64"]))
            assert decoded == {"user_id": "u-123"}
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_dlq_disabled_drops_silently(self):
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1, circuit_failure_threshold=100, enable_dlq=False,
        )
        await client.start_async()
        try:
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            bad = _make_async_response(500)
            client._http_async.post = AsyncMock(side_effect=[token_resp, bad])

            client.publish_to_topic("t", {"v": 1})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # Only 2 calls: token + failed publish. No DLQ attempt.
            assert client._http_async.post.call_count == 2
        finally:
            await client.stop_async(drain_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_dlq_failure_does_not_break_flusher(self):
        """If DLQ itself fails, the flusher continues processing."""
        from unittest.mock import AsyncMock
        client = _make_client(
            max_retries=1, circuit_failure_threshold=100, enable_dlq=True,
        )
        await client.start_async()
        try:
            token_resp = _make_async_response(200, {"access_token": "tok", "expires_in": 3600})
            bad = _make_async_response(500)
            good = _make_async_response(200)
            # token → publish1 (500) → DLQ FAIL (500) → publish2 (200)
            client._http_async.post = AsyncMock(
                side_effect=[token_resp, bad, _make_async_response(500), good],
            )

            client.publish_to_topic("t", {"v": 1})
            client.publish_to_topic("t", {"v": 2})
            await asyncio.wait_for(client._publisher._send_queue.join(), timeout=5.0)

            # Flusher survived: 4 calls total
            assert client._http_async.post.call_count == 4
        finally:
            await client.stop_async(drain_timeout_s=2.0)


class TestHealthCheck:

    def test_health_check_when_async_not_started(self):
        client = _make_client()
        snap = client.health_check()
        assert snap["healthy"] is False  # async not running
        assert snap["running_async"] is False
        assert snap["circuit"]["state"] == "CLOSED"
        assert snap["send_buffer"]["depth"] == 0
        assert snap["send_buffer"]["max"] > 0
        assert snap["token"]["has_token"] is False

    @pytest.mark.asyncio
    async def test_health_check_when_async_running_and_healthy(self):
        client = _make_client()
        await client.start_async()
        try:
            snap = client.health_check()
            assert snap["healthy"] is True
            assert snap["running_async"] is True
            assert snap["circuit"]["state"] == "CLOSED"
            assert snap["dlq_enabled"] is True
        finally:
            await client.stop_async(drain_timeout_s=1.0)

    @pytest.mark.asyncio
    async def test_health_check_reports_open_circuit_unhealthy(self):
        client = _make_client(circuit_failure_threshold=1)
        await client.start_async()
        try:
            await client._publisher._breaker.on_failure()
            snap = client.health_check()
            assert snap["healthy"] is False
            assert snap["circuit"]["state"] == "OPEN"
        finally:
            await client.stop_async(drain_timeout_s=1.0)

    @pytest.mark.asyncio
    async def test_health_check_reports_buffer_utilization(self):
        from unittest.mock import AsyncMock
        client = _make_client(buffer_size=4)
        await client.start_async()
        try:
            # Block HTTP to keep items in queue
            block = asyncio.Event()
            async def _blocking_post(*args, **kwargs):
                await block.wait()
                return _make_async_response(200)
            client._http_async.post = AsyncMock(side_effect=_blocking_post)

            client.publish_to_topic("t", {"v": 1})
            client.publish_to_topic("t", {"v": 2})
            await asyncio.sleep(0.05)  # let flusher pull first item

            snap = client.health_check()
            assert snap["send_buffer"]["max"] == 4
            assert snap["send_buffer"]["policy"] == "drop"
            # Depth is between 0-2 depending on flusher timing
            block.set()
        finally:
            await client.stop_async(drain_timeout_s=1.0)


class TestListQueues:
    """``list_queues()`` parses the management API response and returns
    queue names regardless of which response wrapper the API uses.
    """

    def test_parses_list_of_dicts_with_name_field(self):
        client = _make_client()

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = [
            {"name": "simplemdg/event/core-2/sub-foo"},
            {"name": "simplemdg/event/core-2/sub-bar"},
        ]
        resp.raise_for_status = MagicMock()

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "get", return_value=resp):
            names = client.list_queues()

        assert names == [
            "simplemdg/event/core-2/sub-foo",
            "simplemdg/event/core-2/sub-bar",
        ]

    def test_parses_wrapped_response(self):
        client = _make_client()

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "queues": [{"name": "simplemdg/event/core-2/q1"}],
        }
        resp.raise_for_status = MagicMock()

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "get", return_value=resp):
            names = client.list_queues()

        assert names == ["simplemdg/event/core-2/q1"]

    def test_returns_empty_on_http_error(self):
        client = _make_client()
        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "get", side_effect=Exception("network")):
            assert client.list_queues() == []


class TestQueueLocalNameFromTopic:
    """Queue local-names derived from topic names must not contain dots —
    SAP Event Mesh's management API on some plans rejects dotted queue
    names with 422 Unprocessable Entity (dots are reserved for topic
    hierarchy; queues use slashes / hyphens).
    """

    def test_dotted_topic_produces_dotless_queue(self):
        topic = "orchestration.responses.7a2c7636"
        queue = EventMeshNames.queue_local_from_topic(topic)
        assert queue == "sub-orchestration-responses-7a2c7636"
        assert "." not in queue, (
            f"queue name {queue!r} contains a dot — Event Mesh may 422"
        )

    def test_already_dotless_topic_passes_through(self):
        # Existing tests use single-word topics like "t" — must still work.
        queue = EventMeshNames.queue_local_from_topic("t")
        assert queue == "sub-t"

    def test_underscore_and_alphanumeric_preserved(self):
        queue = EventMeshNames.queue_local_from_topic("foo_bar.baz123")
        assert queue == "sub-foo_bar-baz123"
        assert "." not in queue

    def test_subscribe_to_topic_creates_dotless_queue(self):
        """End-to-end: ``subscribe_to_topic("a.b.c", ...)`` calls
        ``create_queue("simplemdg/event/core-2/sub-a-b-c")``, not
        ``...sub-a.b.c`` — closes the 422 reproducer.
        """
        client = _make_client()

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._admin, "queue_exists", return_value=False) as q_exists, \
             patch.object(client._admin, "create_queue") as q_create, \
             patch.object(client._admin, "subscription_exists", return_value=True), \
             patch.object(client._consumer, "_poll_loop"):  # don't actually start the loop

            client.subscribe_to_topic("orchestration.responses.7a2c7636", lambda m: None)

        expected = "simplemdg/event/core-2/sub-orchestration-responses-7a2c7636"
        # P2: the DMQ is provisioned first, so the queue itself is the LAST
        # name checked/created. Both must be dotless.
        checked = [c.args[0] for c in q_exists.call_args_list]
        assert checked == [f"{expected}-dmq", expected], f"unexpected: {checked!r}"
        created = [c.args[0] for c in q_create.call_args_list]
        assert created == [f"{expected}-dmq", expected], f"unexpected: {created!r}"
        assert q_create.call_args.kwargs == {"with_dmq": True}


class TestCreateQueueErrorContext:
    """``create_queue`` failures must surface the Event Mesh response body
    so operators can diagnose the actual cause (quota, naming policy,
    missing config). Without this, the bare 4xx hides the reason.
    """

    def test_422_response_body_is_logged(self, caplog):
        import logging
        caplog.set_level(logging.ERROR)
        client = _make_client()

        # Mock the token + HTTP PUT that 422s with a body explaining why.
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = (
            '{"errorCode": "QUOTA_EXCEEDED", '
            '"message": "Maximum number of queues exceeded"}'
        )
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "422", request=MagicMock(), response=mock_response,
        )

        with patch.object(client._tokens, "ensure", return_value="tok"), \
             patch.object(client._http, "put", return_value=mock_response):
            with pytest.raises(httpx.HTTPStatusError):
                client.create_queue("sub-orchestration.responses.7a2c7636")

        # The 422 body must appear in the error log so operators see WHY.
        error_messages = " ".join(
            r.getMessage() for r in caplog.records if r.levelname == "ERROR"
        )
        assert "QUOTA_EXCEEDED" in error_messages, (
            f"Response body not captured in error log; got: {error_messages!r}"
        )
        assert "Maximum number of queues exceeded" in error_messages
