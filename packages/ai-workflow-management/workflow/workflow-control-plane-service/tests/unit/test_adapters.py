"""Unit tests for adapter implementations: messaging publishers and dispatchers."""

from __future__ import annotations

import dataclasses
import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from app.layer4_frameworks.messaging.http_task_dispatcher import HttpTaskDispatcher
from app.layer4_frameworks.messaging.http_callback_publisher import HttpCallbackPublisher
from app.layer4_frameworks.messaging.event_mesh_publisher import EventMeshEventPublisher
from app.layer4_frameworks.messaging.console_publisher import ConsoleEventPublisher


# ────────────── HttpTaskDispatcher ──────────────


class TestHttpTaskDispatcher:
    def test_delegates_to_fallback(self):
        fallback = MagicMock()
        dispatcher = HttpTaskDispatcher(fallback, "http://worker:8080")

        dispatcher.publish("some.topic", {"data": 1})

        fallback.publish.assert_called_once_with("some.topic", {"data": 1})

    def test_no_fallback(self):
        dispatcher = HttpTaskDispatcher(None, "http://worker:8080")
        # Should not raise
        dispatcher.publish("some.topic", {"data": 1})

    def test_dispatch_http_called_for_task_dispatch(self):
        dispatcher = HttpTaskDispatcher(None, "http://worker:8080")
        mock_response = MagicMock()
        mock_response.status_code = 200
        dispatcher._client = MagicMock()
        dispatcher._client.post.return_value = mock_response

        payload = {"task_id": "t1", "run_id": "r1"}

        # Call _dispatch_http directly (normally on a thread pool)
        dispatcher._dispatch_http(payload)

        dispatcher._client.post.assert_called_once_with(
            "http://worker:8080/api/v1/tasks/execute",
            json=payload,
        )

    def test_dispatch_http_handles_exception(self):
        dispatcher = HttpTaskDispatcher(None, "http://worker:8080")
        dispatcher._client = MagicMock()
        dispatcher._client.post.side_effect = Exception("connection error")

        # Should not raise
        dispatcher._dispatch_http({"task_id": "t1"})

    def test_task_dispatch_topic_submits_to_executor(self):
        fallback = MagicMock()
        dispatcher = HttpTaskDispatcher(fallback, "http://worker:8080")
        dispatcher._executor = MagicMock()

        # Flattened-dict shape produced by EnrichingEventPublisher
        # after serializing a TaskDispatched dataclass.
        event = {
            "task_id": "t1",
            "run_id": "r1",
            "node_id": "n1",
            "worker_type": "test-worker",
            "input": {"key": "val"},
            "node_config": {},
            "idempotency_key": "ik1",
        }

        dispatcher.publish("task.dispatch", event)
        dispatcher._executor.submit.assert_called_once()
        payload = dispatcher._executor.submit.call_args[0][1]
        assert payload["task_id"] == "t1"
        assert payload["worker_type"] == "test-worker"
        assert payload["input"] == {"key": "val"}

    def test_task_dispatched_topic_submits_to_executor(self):
        from app.layer1_domain.events import NodeDispatched

        fallback = MagicMock()
        dispatcher = HttpTaskDispatcher(fallback, "http://worker:8080")
        dispatcher._executor = MagicMock()

        event = NodeDispatched(
            task_id="t1", run_id="r1", node_id="n1",
            worker_type="http-request", input={"key": "val"},
            node_config={"timeout": 30}, idempotency_key="ik1",
        )

        dispatcher.publish("node.dispatched", event)
        dispatcher._executor.submit.assert_called_once()
        call_args = dispatcher._executor.submit.call_args
        payload = call_args[0][1]  # second positional arg to submit(fn, payload)
        assert payload["task_id"] == "t1"
        assert payload["input"] == {"key": "val"}

    def test_non_task_dispatch_no_submit(self):
        fallback = MagicMock()
        dispatcher = HttpTaskDispatcher(fallback, "http://worker:8080")
        dispatcher._executor = MagicMock()

        dispatcher.publish("other.topic", {"data": 1})
        dispatcher._executor.submit.assert_not_called()

    def test_non_dispatchable_event_no_submit(self):
        fallback = MagicMock()
        dispatcher = HttpTaskDispatcher(fallback, "http://worker:8080")
        dispatcher._executor = MagicMock()

        dispatcher.publish("node.dispatched", "not a dict or dataclass")
        dispatcher._executor.submit.assert_not_called()

    def test_url_trailing_slash_stripped(self):
        dispatcher = HttpTaskDispatcher(None, "http://worker:8080/")
        assert dispatcher._worker_execution_url == "http://worker:8080"


# ────────────── HttpCallbackPublisher ──────────────


class TestHttpCallbackPublisher:
    def test_post_callback_success(self):
        publisher = HttpCallbackPublisher("http://agent:9090")
        mock_response = MagicMock()
        mock_response.status_code = 200
        publisher._client = MagicMock()
        publisher._client.post.return_value = mock_response

        publisher._post_callback({"correlation_id": "corr-1", "status": "COMPLETED"})

        publisher._client.post.assert_called_once()
        url = publisher._client.post.call_args[0][0]
        assert url == "http://agent:9090/callbacks/workflow-completion"

    def test_post_callback_failure(self):
        publisher = HttpCallbackPublisher("http://agent:9090")
        publisher._client = MagicMock()
        publisher._client.post.side_effect = Exception("timeout")

        # Should not raise
        publisher._post_callback({"correlation_id": "corr-1"})

    def test_publish_submits_to_executor(self):
        publisher = HttpCallbackPublisher("http://agent:9090")
        publisher._executor = MagicMock()

        publisher.publish("topic", {"status": "COMPLETED"})
        publisher._executor.submit.assert_called_once()

    def test_url_trailing_slash_stripped(self):
        publisher = HttpCallbackPublisher("http://agent:9090/")
        assert publisher._agent_url == "http://agent:9090"

    def test_custom_timeout(self):
        publisher = HttpCallbackPublisher("http://agent:9090", timeout=60.0)
        assert publisher._timeout == 60.0


# ────────────── EventMeshEventPublisher ──────────────


class TestEventMeshPublisher:
    def test_publish_dataclass_event(self):
        @dataclasses.dataclass
        class TestEvent:
            run_id: str = "r1"
            status: str = "ok"

        client = MagicMock()
        publisher = EventMeshEventPublisher(client)

        event = TestEvent()
        publisher.publish("test.topic", event)

        client.publish_to_topic.assert_called_once()
        call_args = client.publish_to_topic.call_args
        assert call_args[0][0] == "test.topic"
        assert call_args[0][1] == {"run_id": "r1", "status": "ok"}

    def test_publish_dict_event(self):
        client = MagicMock()
        publisher = EventMeshEventPublisher(client)

        publisher.publish("test.topic", {"key": "val"})

        client.publish_to_topic.assert_called_once_with("test.topic", {"key": "val"})

    def test_publish_non_dict_non_dataclass(self):
        client = MagicMock()
        publisher = EventMeshEventPublisher(client)

        publisher.publish("test.topic", "some string")

        client.publish_to_topic.assert_called_once_with("test.topic", {"data": "some string"})

    def test_publish_int_event(self):
        client = MagicMock()
        publisher = EventMeshEventPublisher(client)

        publisher.publish("test.topic", 42)

        client.publish_to_topic.assert_called_once_with("test.topic", {"data": "42"})

    def test_publish_dataclass_type_not_instance(self):
        """Passing a dataclass *type* (not instance) should be treated as non-dataclass."""
        @dataclasses.dataclass
        class TestEvent:
            run_id: str = "r1"

        client = MagicMock()
        publisher = EventMeshEventPublisher(client)

        publisher.publish("test.topic", TestEvent)

        call_args = client.publish_to_topic.call_args
        assert call_args[0][0] == "test.topic"
        # type object => {"data": str(TestEvent)}
        assert "data" in call_args[0][1]

    def test_publish_only_to_original_topic(self):
        """EventMeshEventPublisher now only publishes to the original topic.
        Centralized topic logic is handled by EnrichingEventPublisher decorator."""
        client = MagicMock()
        publisher = EventMeshEventPublisher(client)

        publisher.publish("run.started", {"run_id": "r1", "status": "started"})

        client.publish_to_topic.assert_called_once_with(
            "run.started", {"run_id": "r1", "status": "started"},
        )
        client.publish_to_queue.assert_not_called()


# ────────────── ConsoleEventPublisher ──────────────


class TestConsolePublisher:
    def test_publish_logs(self, caplog):
        publisher = ConsoleEventPublisher()
        with caplog.at_level(logging.DEBUG):
            publisher.publish("test.topic", {"key": "val"})

        assert "test.topic" in caplog.text
        assert "key" in caplog.text

    def test_publish_string_event(self, caplog):
        publisher = ConsoleEventPublisher()
        with caplog.at_level(logging.DEBUG):
            publisher.publish("my.topic", "hello")

        assert "my.topic" in caplog.text
        assert "hello" in caplog.text
