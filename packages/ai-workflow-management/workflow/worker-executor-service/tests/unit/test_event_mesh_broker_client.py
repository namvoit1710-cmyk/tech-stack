"""Unit tests for EventMeshBrokerClient.

All HTTP calls are mocked — no real Event Mesh endpoint is needed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging.event_mesh_broker_client import (
    EventMeshBrokerClient,
)
from app.layer4_frameworks.messaging.event_mesh_config import EventMeshConfig


def _make_client(**overrides) -> EventMeshBrokerClient:
    defaults = dict(
        token_url="https://auth.example.com/oauth/token",
        messaging_url="https://messaging.example.com",
        management_url="https://management.example.com",
        client_id="test-id",
        client_secret="test-secret",
        namespace="simplemdg/event/core-2",
    )
    defaults.update(overrides)
    return EventMeshBrokerClient(EventMeshConfig(**defaults))


def _mock_token_response():
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
        assert client._qualify_topic("run.started") == "simplemdg/event/core-2/run.started"

    def test_qualify_topic_no_double_prefix(self):
        client = _make_client()
        already = "simplemdg/event/core-2/run.started"
        assert client._qualify_topic(already) == already

    def test_qualify_topic_empty_namespace(self):
        client = _make_client(namespace="")
        assert client._qualify_topic("run.started") == "run.started"

    def test_qualify_queue_prepends_namespace(self):
        client = _make_client()
        assert client._qualify_queue("workflow.tasks") == "simplemdg/event/core-2/workflow.tasks"

    def test_qualify_queue_no_double_prefix(self):
        client = _make_client()
        already = "simplemdg/event/core-2/workflow.tasks"
        assert client._qualify_queue(already) == already

    def test_qualify_topic_rewrites_underscore_to_hyphen(self):
        # Classic Event Mesh rejects an underscore in a topic level
        # ("Topic is invalid"); map _ -> - on the wire so the subscription
        # binds to the same name the control plane publishes to.
        client = _make_client()
        assert (
            client._qualify_topic("child_run.started")
            == "simplemdg/event/core-2/child-run.started"
        )

    def test_qualify_topic_underscore_free_unchanged(self):
        client = _make_client()
        assert client._qualify_topic("run.status") == "simplemdg/event/core-2/run.status"


# ---------------------------------------------------------------------------
# OAuth2 token management
# ---------------------------------------------------------------------------


class TestTokenManagement:

    def test_refresh_token_calls_token_url(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        token = client._ensure_token()

        assert token == "tok-123"
        client._http.post.assert_called_once()
        call_args = client._http.post.call_args
        assert call_args[0][0] == "https://auth.example.com/oauth/token"
        assert call_args[1]["data"]["grant_type"] == "client_credentials"

    def test_token_cached_on_second_call(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        client._ensure_token()
        client._ensure_token()

        assert client._http.post.call_count == 1

    def test_token_refresh_on_expiry(self):
        client = _make_client()
        client._http = MagicMock()
        client._http.post.return_value = _mock_token_response()

        client._ensure_token()
        client._token_expires_at = 0
        client._ensure_token()

        assert client._http.post.call_count == 2


# ---------------------------------------------------------------------------
# publish_to_topic
# ---------------------------------------------------------------------------


class TestPublishToTopic:

    def test_publish_qualifies_topic_with_namespace(self):
        client = _make_client()
        client._http = MagicMock()
        publish_resp = MagicMock()
        publish_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), publish_resp]

        client.publish_to_topic("run.started", {"run_id": "r1"})

        publish_call = client._http.post.call_args_list[1]
        url = publish_call[0][0]
        assert "simplemdg%2Fevent%2Fcore-2%2Frun.started" in url
        payload = json.loads(publish_call[1]["content"])
        assert payload == {"run_id": "r1"}

    def test_publish_with_key(self):
        client = _make_client()
        client._http = MagicMock()
        publish_resp = MagicMock()
        publish_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), publish_resp]

        client.publish_to_topic("t", {"data": 1}, key="my-key")

        publish_call = client._http.post.call_args_list[1]
        headers = publish_call[1]["headers"]
        assert headers["x-message-key"] == "my-key"

    def test_publish_no_namespace(self):
        client = _make_client(namespace="")
        client._http = MagicMock()
        publish_resp = MagicMock()
        publish_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), publish_resp]

        client.publish_to_topic("run.started", {"data": 1})

        url = client._http.post.call_args_list[1][0][0]
        assert "/topics/run.started/messages" in url


# ---------------------------------------------------------------------------
# Queue-based methods
# ---------------------------------------------------------------------------


class TestQueueMethods:

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

    def test_publish_to_queue_qualifies_name(self):
        client = _make_client()
        client._http = MagicMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), resp]

        client.publish_to_queue("my-queue", {"task": "t1"})

        url = client._http.post.call_args_list[1][0][0]
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-queue" in url

    def test_acknowledge_qualifies_queue(self):
        client = _make_client()
        client._http = MagicMock()
        ack_resp = MagicMock()
        ack_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), ack_resp]

        client.acknowledge("workflow.tasks", "msg-42")

        url = client._http.post.call_args_list[1][0][0]
        assert "simplemdg%2Fevent%2Fcore-2%2Fworkflow.tasks" in url
        assert "/messages/msg-42/acknowledgement" in url

    def test_publish_to_dlq(self):
        client = _make_client()
        client._http = MagicMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), resp]

        client.publish_to_dlq("workflow.tasks", {"task_id": "t1"}, "processing error")

        publish_call = client._http.post.call_args_list[1]
        payload = json.loads(publish_call[1]["content"])
        assert payload["original_topic"] == "workflow.tasks"
        assert payload["error"] == "processing error"


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
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-queue" in url

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

    def test_delete_subscription_qualifies_both(self):
        client = _make_client()
        client._http = MagicMock()
        del_resp = MagicMock()
        del_resp.raise_for_status = MagicMock()
        client._http.post.return_value = _mock_token_response()
        client._http.delete.return_value = del_resp

        client.delete_subscription("my-queue", "my-topic")

        url = client._http.delete.call_args[0][0]
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-queue" in url
        assert "simplemdg%2Fevent%2Fcore-2%2Fmy-topic" in url


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:

    def test_close_stops_and_closes_http(self):
        client = _make_client()
        client._http = MagicMock()

        client.close()

        assert client._running is False
        client._http.close.assert_called_once()


# ---------------------------------------------------------------------------
# Integration with EventMesh task queue and result publisher
# ---------------------------------------------------------------------------


class TestEventMeshAdaptersIntegration:

    def test_task_queue_consume_via_broker(self):
        from app.layer4_frameworks.messaging.event_mesh_task_queue import EventMeshTaskQueue

        client = _make_client()
        client.subscribe_to_topic = MagicMock()

        def cb(data):
            pass

        queue = EventMeshTaskQueue(client)
        queue.consume(cb)

        client.subscribe_to_topic.assert_called_once_with("workflow.tasks", cb)

    def test_result_publisher_via_broker(self):
        from app.layer4_frameworks.messaging.event_mesh_result_publisher import EventMeshResultPublisher

        client = _make_client()
        client._http = MagicMock()
        publish_resp = MagicMock()
        publish_resp.raise_for_status = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), publish_resp]

        pub = EventMeshResultPublisher(client)
        pub.publish_result({"task_id": "t1", "status": "COMPLETED"})

        publish_call = client._http.post.call_args_list[1]
        url = publish_call[0][0]
        # Topic should be namespace-qualified
        assert "simplemdg%2Fevent%2Fcore-2%2Fworkflow.results" in url
        payload = json.loads(publish_call[1]["content"])
        assert payload["task_id"] == "t1"


# ---------------------------------------------------------------------------
# Poll loop: batch-drain + idle-only sleep (throughput fix)
# ---------------------------------------------------------------------------


class TestPollLoopBatchDrain:
    """``_poll_once`` drains up to ``poll_batch_size`` messages per queue in a
    single pass (per-message ack preserved) and reports whether anything was
    handled, so ``_poll_loop`` sleeps only when every queue is empty — instead
    of the old one-message-per-0.5s ceiling."""

    def _wire(self, client, messages):
        """Register a recording handler on 'q1'; feed `messages` then None."""
        import asyncio

        client._running = True
        handled: list = []
        acked: list = []
        seq = iter(list(messages) + [None])
        client.consume_from_queue = lambda q: next(seq)
        client.acknowledge = lambda q, mid: acked.append(mid)
        client._callbacks = {"q1": [lambda data: handled.append(data)]}
        loop = asyncio.new_event_loop()
        return loop, handled, acked

    def test_drains_whole_backlog_in_one_pass_and_acks_each(self):
        client = _make_client(poll_batch_size=10)
        msgs = [{"data": {"n": i}, "message_id": f"m{i}"} for i in range(3)]
        loop, handled, acked = self._wire(client, msgs)
        try:
            had = client._poll_once()
        finally:
            loop.close()

        assert had is True
        assert handled == [{"n": 0}, {"n": 1}, {"n": 2}]  # drained back-to-back
        assert acked == ["m0", "m1", "m2"]                # per-message ack kept

    def test_empty_pass_returns_false_so_caller_sleeps(self):
        client = _make_client()
        loop, handled, acked = self._wire(client, [])  # immediately None
        try:
            had = client._poll_once()
        finally:
            loop.close()

        assert had is False
        assert handled == []
        assert acked == []

    def test_batch_size_caps_messages_per_pass(self):
        client = _make_client(poll_batch_size=2)
        # 5 available, but only batch_size=2 drained this pass.
        msgs = [{"data": {"n": i}, "message_id": f"m{i}"} for i in range(5)]
        import asyncio

        client._running = True
        handled: list = []
        seq = iter(msgs)  # never exhausts within 2 calls
        client.consume_from_queue = lambda q: next(seq)
        client.acknowledge = lambda q, mid: None
        client._callbacks = {"q1": [lambda data: handled.append(data)]}
        loop = asyncio.new_event_loop()
        try:
            had = client._poll_once()
        finally:
            loop.close()

        assert had is True
        assert len(handled) == 2  # capped by poll_batch_size

    def test_handler_failure_skips_ack_for_redelivery(self):
        """At-least-once: a handler exception (e.g. the executor's backpressure
        signal at capacity) must NOT ack — Event Mesh redelivers after the lock
        TTL instead of dropping the task."""
        import asyncio

        client = _make_client(poll_batch_size=10)
        client._running = True
        acked: list = []
        seq = iter([{"data": {"n": 1}, "message_id": "m1"}, None])
        client.consume_from_queue = lambda q: next(seq)
        client.acknowledge = lambda q, mid: acked.append(mid)

        def _boom(_data):
            raise RuntimeError("backpressure: at capacity")

        client._callbacks = {"q1": [_boom]}
        loop = asyncio.new_event_loop()
        try:
            had = client._poll_once()
        finally:
            loop.close()

        assert had is True   # a message was handled (attempted)
        assert acked == []   # but NOT acked → redelivers


class TestConsumeLockDuration:
    def test_consume_sets_lock_duration_header(self):
        """The consume request carries ``x-lock-duration-in-ms`` so a message
        being processed under concurrency isn't redelivered mid-flight."""
        client = _make_client(lock_duration_ms=45000)
        consume_resp = MagicMock()
        consume_resp.status_code = 204  # no message; we only inspect the request
        consume_resp.raise_for_status = MagicMock()
        client._http = MagicMock()
        client._http.post.side_effect = [_mock_token_response(), consume_resp]

        client.consume_from_queue("q1")

        consume_call = client._http.post.call_args_list[1]
        headers = consume_call[1]["headers"]
        assert headers["x-lock-duration-in-ms"] == "45000"
