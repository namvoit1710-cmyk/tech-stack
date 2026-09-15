"""Phase M.3.3 — ResponseTopicListener unit tests.

Verifies the listener rehydrates EditNodeDataResult from the wire
payload, resolves/fails the local ResponseRegistry, and subscribes to
the correct per-instance topic.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
    EditNodeDataResult,
    OpResult,
)
from app.layer4_frameworks.orchestration_queue.response_registry import ResponseRegistry
from app.layer4_frameworks.orchestration_queue.response_topic_listener import (
    CrossInstanceExecutionError,
    ResponseTopicListener,
    response_topic_for,
    _METRIC_DEGRADED,
    _METRIC_SUBSCRIBE,
)


class TestTopicNaming:
    # SA-2087 (R12): the shard is a sha256 PREFIX, not a truncation of the id.
    # Truncation assumed ids differ in their first bytes — true for uuid4,
    # false for a k8s Deployment pod name, where every replica of
    # ``wio-executor`` collapsed onto one shared topic. Golden values below are
    # computed independently; the collision cases live in
    # ``test_response_topic_identity.py``.

    def test_response_topic_for_short_instance_id_is_hashed(self):
        """Short ids are hashed like any other — the old behaviour kept them
        intact (dashes stripped), which made short ids sharing a prefix
        collide."""
        assert response_topic_for("abc-123") == "orchestration.responses.5942d94f"

    def test_response_topic_for_uuid_is_hashed_not_truncated(self):
        """A uuid no longer contributes its own first 8 hex chars; the shard is
        sha256(id)[:8], so the Event Mesh 60-char budget still holds while the
        first-bytes assumption is gone."""
        uuid_v4 = "5cf86206-c663-4de7-8bd4-5203c81f6bd8"
        assert response_topic_for(uuid_v4) == "orchestration.responses.ae266e7c"

    def test_qualified_queue_name_fits_event_mesh_60_char_local_limit(self):
        """End-to-end length budget: even with the longest realistic UUID and
        the longest realistic namespace prefix, the queue name must stay under
        60 chars locally to avoid SAP Event Mesh 422 Unprocessable Entity.

        Qualified queue = ``sub-<topic>`` (the broker adds the namespace
        prefix outside this scope, on top of this local name).
        """
        uuid_v4 = "5cf86206-c663-4de7-8bd4-5203c81f6bd8"
        topic = response_topic_for(uuid_v4)
        local_queue = f"sub-{topic}"
        # Local queue must fit in 60 chars regardless of namespace prefix.
        assert len(local_queue) <= 60, (
            f"local_queue={local_queue!r} exceeds 60-char Event Mesh limit"
        )

    def test_response_topic_for_empty_id_returns_empty_shard(self):
        assert response_topic_for("") == "orchestration.responses."


class TestListenerStart:
    def test_start_subscribes_to_own_topic(self):
        broker = MagicMock()
        registry = ResponseRegistry()
        listener = ResponseTopicListener(broker, "inst-1", registry)

        listener.start()

        broker.subscribe_to_topic.assert_called_once()
        topic, cb = broker.subscribe_to_topic.call_args.args
        # SA-2087: shard = sha256("inst-1")[:8] = 81bf9124 (was the compacted "inst1").
        assert topic == "orchestration.responses.81bf9124"
        assert callable(cb)

    def test_start_is_idempotent(self):
        broker = MagicMock()
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())
        listener.start()
        listener.start()
        assert broker.subscribe_to_topic.call_count == 1

    def test_start_ensures_topic_when_supported(self):
        broker = MagicMock(spec=["publish_to_topic", "subscribe_to_topic", "ensure_topic"])
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())
        listener.start()
        broker.ensure_topic.assert_called_once_with("orchestration.responses.81bf9124")


class TestGracefulDegradation:
    """``start()`` must not crash the lifespan when the broker can't
    provision the per-instance queue (e.g. SAP Event Mesh 422 from a
    plan quota or naming-policy violation). The listener flips into a
    degraded state — the local fast-path keeps working, multi-pod
    operators see the warning and investigate.
    """

    def test_subscribe_failure_does_not_raise(self):
        broker = MagicMock()
        broker.subscribe_to_topic.side_effect = RuntimeError(
            "Event Mesh create_queue failed: 422 Unprocessable Entity"
        )
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())

        # Must not raise — single-pod deployments keep starting.
        listener.start()

        assert listener.degraded is True
        # Subscription was attempted, just failed.
        broker.subscribe_to_topic.assert_called_once()

    def test_successful_subscribe_keeps_degraded_false(self):
        broker = MagicMock()
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())
        listener.start()
        assert listener.degraded is False

    def test_ensure_topic_failure_alone_does_not_degrade_listener(self):
        """``ensure_topic`` failure was already non-fatal pre-change —
        only ``subscribe_to_topic`` failure should flip the degraded flag.
        """
        broker = MagicMock(spec=["publish_to_topic", "subscribe_to_topic", "ensure_topic"])
        broker.ensure_topic.side_effect = RuntimeError("topic create failed")
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())

        listener.start()

        # ensure_topic failed but subscribe succeeded → not degraded
        assert listener.degraded is False
        broker.subscribe_to_topic.assert_called_once()


@pytest.mark.asyncio
class TestOnMessageResolution:
    async def test_resolves_future_with_rehydrated_result(self):
        broker = MagicMock()
        registry = ResponseRegistry()
        listener = ResponseTopicListener(broker, "inst-1", registry)

        correlation_id, future = registry.allocate()

        payload = {
            "correlation_id": correlation_id,
            "result": {
                "etag": "e-1", "version": 3,
                "file_lineage": {"a": "b"},
                "results": [{
                    "artifact_id": "__root__",
                    "op_index": 0, "status": "ok",
                    "rows_affected": 1, "row_ids": ["r1"], "error": None,
                }],
                "error": None, "error_code": "", "error_details": {},
            },
            "error": None,
        }
        listener._on_message(payload)

        result = await asyncio.wait_for(future, timeout=1.0)
        assert isinstance(result, EditNodeDataResult)
        assert result.etag == "e-1"
        assert result.version == 3
        assert len(result.results) == 1
        assert isinstance(result.results[0], OpResult)

        registry.release(correlation_id)

    async def test_fails_future_on_error_payload(self):
        broker = MagicMock()
        registry = ResponseRegistry()
        listener = ResponseTopicListener(broker, "inst-1", registry)

        correlation_id, future = registry.allocate()

        listener._on_message({
            "correlation_id": correlation_id,
            "result": None,
            "error": {"class": "ValueError", "message": "boom"},
        })

        with pytest.raises(CrossInstanceExecutionError) as excinfo:
            await asyncio.wait_for(future, timeout=1.0)
        assert excinfo.value.exception_class == "ValueError"
        assert "boom" in str(excinfo.value)

        registry.release(correlation_id)

    async def test_missing_correlation_id_is_ignored(self):
        broker = MagicMock()
        registry = ResponseRegistry()
        listener = ResponseTopicListener(broker, "inst-1", registry)
        # No raise; just logs and returns. If it raised, subscriber would crash.
        listener._on_message({"result": {}, "error": None})

    async def test_unknown_correlation_id_is_silently_dropped(self):
        broker = MagicMock()
        registry = ResponseRegistry()
        listener = ResponseTopicListener(broker, "inst-1", registry)
        # Valid shape but nobody is waiting — registry.resolve is a no-op.
        listener._on_message({
            "correlation_id": "stale-corr",
            "result": {
                "etag": "", "version": 0, "file_lineage": {},
                "results": [], "rerun": {
                    "strategy": "none", "triggered": False,
                    "degraded_from": None, "reset_node_ids": [], "task_ids": [],
                },
                "error": None, "error_code": "", "error_details": {},
            },
            "error": None,
        })


class TestObservability:
    """SA-1466 — degradation must be observable (metric) + queryable (health())."""

    def test_degraded_emits_metric(self):
        broker = MagicMock()
        broker.subscribe_to_topic.side_effect = RuntimeError("422 quota")
        monitor = MagicMock()
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry(), monitor=monitor)

        listener.start()

        assert listener.degraded is True
        monitor.track.assert_called_once_with(_METRIC_DEGRADED, 1.0)
        # subscribe counter recorded the degraded outcome
        name, kwargs = monitor.increment.call_args.args[0], monitor.increment.call_args.kwargs
        assert name == _METRIC_SUBSCRIBE
        assert kwargs["tags"]["result"] == "degraded"
        assert kwargs["tags"]["reason"] == "RuntimeError"

    def test_healthy_emits_gauge_zero(self):
        broker = MagicMock()
        monitor = MagicMock()
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry(), monitor=monitor)

        listener.start()

        assert listener.degraded is False
        monitor.track.assert_called_once_with(_METRIC_DEGRADED, 0.0)
        assert monitor.increment.call_args.kwargs["tags"]["result"] == "ok"

    def test_monitor_none_is_safe(self):
        broker = MagicMock()
        # No monitor wired — must not raise on the metric path.
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())
        listener.start()
        assert listener.degraded is False

    def test_monitor_exception_never_breaks_start(self):
        broker = MagicMock()
        monitor = MagicMock()
        monitor.track.side_effect = RuntimeError("monitor backend down")
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry(), monitor=monitor)
        # Must not raise even though the monitor blows up.
        listener.start()
        assert listener.degraded is False  # subscribe itself succeeded

    def test_health_snapshot(self):
        broker = MagicMock()
        listener = ResponseTopicListener(broker, "inst-1", ResponseRegistry())
        listener.start()
        h = listener.health()
        assert h["present"] is True
        assert h["topic"] == "orchestration.responses.81bf9124"
        assert h["instance_id"] == "inst-1"
        assert h["started"] is True
        assert h["degraded"] is False


class TestReadinessEndpoint:
    """SA-1466 — /api/v1/health/readiness surfaces + optionally gates on the
    listener's degraded state."""

    class _Req:
        def __init__(self, container):
            self.app = type("A", (), {"state": type("S", (), {"container": container})()})()

    class _StubListener:
        def __init__(self, degraded):
            self._d = degraded

        @property
        def degraded(self):
            return self._d

        def health(self):
            return {"present": True, "degraded": self._d, "topic": "t",
                    "instance_id": "i", "started": True}

    def _call(self, container):
        from fastapi import Response
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        resp = Response()
        body = health_controller.readiness_check(self._Req(container), resp)
        return body, resp.status_code

    def test_gate_off_degraded_stays_ready_200(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings,
                            "RESPONSE_LISTENER_READINESS_GATE_ENABLED", False)
        body, status = self._call({"_response_topic_listener": self._StubListener(True)})
        assert body["ready"] is True
        assert status == 200
        assert body["response_topic_listener"]["degraded"] is True

    def test_gate_on_degraded_returns_503(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings,
                            "RESPONSE_LISTENER_READINESS_GATE_ENABLED", True)
        body, status = self._call({"_response_topic_listener": self._StubListener(True)})
        assert body["ready"] is False
        assert status == 503
        assert "response_topic_listener_degraded" in body["reasons"]

    def test_gate_on_healthy_is_200(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings,
                            "RESPONSE_LISTENER_READINESS_GATE_ENABLED", True)
        body, status = self._call({"_response_topic_listener": self._StubListener(False)})
        assert body["ready"] is True
        assert status == 200

    def test_no_listener_present_is_ready(self, monkeypatch):
        from app.layer3_adapters.controllers.restful.v1 import health_controller
        monkeypatch.setattr(health_controller.settings,
                            "RESPONSE_LISTENER_READINESS_GATE_ENABLED", True)
        body, status = self._call({"_response_topic_listener": None})
        assert body["ready"] is True
        assert status == 200
        assert body["response_topic_listener"]["present"] is False
