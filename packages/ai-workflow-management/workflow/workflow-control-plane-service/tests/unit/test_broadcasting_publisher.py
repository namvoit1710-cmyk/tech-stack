"""Tests for RealtimeBroadcastingPublisher (broker-backed)."""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

from app.layer4_frameworks.realtime.broadcasting_publisher import (
    REALTIME_TOPIC,
    RealtimeBroadcastingPublisher,
    _extract_run_id,
    _normalize_topic,
)


@dataclass
class _FakeEvent:
    run_id: str = "run-1"
    node_id: str = "n1"


@dataclass
class _NoRunIdEvent:
    node_id: str = "n1"


@dataclass
class _NestedEvent:
    run_id: str = "child-1"
    parent_run_id: str = "parent-1"
    root_run_id: str = "root-1"
    workflow_id: str = "wf-1"


class TestNormalizeTopic:
    def test_dot_separated_passes_through(self):
        assert _normalize_topic("run.started") == "run.started"

    def test_lowered_class_name_mapped(self):
        assert _normalize_topic("runstatuschanged") == "run.status"
        assert _normalize_topic("nodecompleted") == "node.completed"

    def test_unknown_topic_returned_as_is(self):
        assert _normalize_topic("custom_topic") == "custom_topic"


class TestExtractRunId:
    def test_from_dataclass(self):
        assert _extract_run_id(_FakeEvent(run_id="abc")) == "abc"

    def test_from_dict_top_level(self):
        assert _extract_run_id({"run_id": "r1"}) == "r1"

    def test_from_dict_nested_payload(self):
        assert _extract_run_id({"payload": {"run_id": "r2"}}) == "r2"

    def test_missing_run_id_returns_none(self):
        assert _extract_run_id(_NoRunIdEvent()) is None
        assert _extract_run_id({"some_key": "val"}) is None

    def test_empty_run_id_returns_none(self):
        assert _extract_run_id(_FakeEvent(run_id="")) is None


class TestRealtimeBroadcastingPublisher:
    def _make(self):
        inner = MagicMock()
        broker = MagicMock()
        pub = RealtimeBroadcastingPublisher(inner, broker)
        return pub, inner, broker

    def test_delegates_to_inner_publisher(self):
        pub, inner, broker = self._make()
        event = _FakeEvent(run_id="r1")
        pub.publish("runstarted", event)
        inner.publish.assert_called_once_with("runstarted", event)

    def test_broker_publish_envelope_structure(self):
        pub, inner, broker = self._make()
        event = _FakeEvent(run_id="r1")
        pub.publish("runstatuschanged", event)

        broker.publish_to_topic.assert_called_once()
        args, kwargs = broker.publish_to_topic.call_args
        assert args[0] == REALTIME_TOPIC
        envelope = args[1]
        assert envelope["event_type"] == "run.status"
        assert envelope["rooms"] == ["run:r1"]
        assert envelope["payload"]["run_id"] == "r1"
        assert envelope["payload"]["_event"] == "run.status"
        assert kwargs["key"] == "r1"

    def test_partition_key_prefers_root_run_id(self):
        pub, inner, broker = self._make()
        pub.publish("taskdispatched", _NestedEvent())
        _, kwargs = broker.publish_to_topic.call_args
        assert kwargs["key"] == "root-1"

    def test_nested_event_broadcasts_to_all_rooms_in_order(self):
        pub, inner, broker = self._make()
        pub.publish("inputrequested", _NestedEvent())
        envelope = broker.publish_to_topic.call_args[0][1]
        # Workflow-scoped rooms were retired (see _collect_rooms): realtime
        # fan-out is run-scoped only (child → parent → root).
        assert envelope["rooms"] == [
            "run:child-1",
            "run:parent-1",
            "run:root-1",
        ]

    def test_dict_event_broadcast(self):
        pub, inner, broker = self._make()
        pub.publish("task.dispatch", {"run_id": "r2", "status": "ok"})
        envelope = broker.publish_to_topic.call_args[0][1]
        assert envelope["event_type"] == "task.dispatch"
        assert envelope["rooms"] == ["run:r2"]

    def test_nested_payload_run_id(self):
        pub, inner, broker = self._make()
        pub.publish("task.dispatch", {"payload": {"run_id": "r3"}})
        envelope = broker.publish_to_topic.call_args[0][1]
        assert envelope["rooms"] == ["run:r3"]

    def test_no_ids_skips_broker_publish(self):
        pub, inner, broker = self._make()
        pub.publish("runstarted", _NoRunIdEvent())
        inner.publish.assert_called_once()
        broker.publish_to_topic.assert_not_called()

    def test_broker_error_does_not_propagate(self):
        pub, inner, broker = self._make()
        broker.publish_to_topic.side_effect = RuntimeError("boom")
        pub.publish("runstarted", _FakeEvent(run_id="r1"))
        inner.publish.assert_called_once()

    def test_inner_error_does_not_block_broker(self):
        pub, inner, broker = self._make()
        inner.publish.side_effect = RuntimeError("inner broke")
        pub.publish("runstarted", _FakeEvent(run_id="r1"))
        broker.publish_to_topic.assert_called_once()

    def test_none_broker_skips_realtime(self):
        inner = MagicMock()
        pub = RealtimeBroadcastingPublisher(inner, broker=None)
        pub.publish("runstarted", _FakeEvent(run_id="r1"))
        inner.publish.assert_called_once()

    def test_none_broker_direct_notifies_all_consumers(self):
        inner = MagicMock()
        consumer_a = MagicMock()
        consumer_b = MagicMock()
        pub = RealtimeBroadcastingPublisher(inner, broker=None)

        pub.set_realtime_consumer(consumer_a)
        pub.add_realtime_consumer(consumer_b)

        pub.publish("runstatuschanged", _FakeEvent(run_id="r1"))

        consumer_a.notify.assert_called_once()
        consumer_b.notify.assert_called_once()
        envelope = consumer_a.notify.call_args[0][0]
        assert envelope["event_type"] == "run.status"
        assert envelope["rooms"] == ["run:r1"]

    def test_none_inner_still_publishes_to_broker(self):
        broker = MagicMock()
        pub = RealtimeBroadcastingPublisher(None, broker)
        pub.publish("runstarted", _FakeEvent(run_id="r1"))
        broker.publish_to_topic.assert_called_once()
