"""Phase M.3.4 — per-run partition keys on the domain-event Kafka path.

The rule mirrors ``RealtimeBroadcastingPublisher``:
``root_run_id > run_id > workflow_id > None``. Same rule on both chains
means all events for a single run land on the same Kafka partition, so
per-run ordering composes end-to-end.
"""
from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from app.layer4_frameworks.messaging.kafka_event_publisher import (
    KafkaEventPublisher,
    extract_partition_key,
)


class TestExtractPartitionKey:
    def test_prefers_root_run_id(self):
        key = extract_partition_key({
            "root_run_id": "root-1",
            "run_id": "child-2",
            "workflow_id": "wf-3",
        })
        assert key == "root-1"

    def test_falls_back_to_run_id_when_root_absent(self):
        assert extract_partition_key({"run_id": "r-1", "workflow_id": "wf-1"}) == "r-1"

    def test_falls_back_to_workflow_id(self):
        assert extract_partition_key({"workflow_id": "wf-1"}) == "wf-1"

    def test_returns_none_when_no_key_fields(self):
        assert extract_partition_key({"foo": "bar"}) is None

    def test_skips_empty_string_values(self):
        assert extract_partition_key({"root_run_id": "", "run_id": "r-1"}) == "r-1"

    def test_coerces_to_string(self):
        # Kafka keys must be strings; guard against integer run_ids slipping in.
        assert extract_partition_key({"run_id": 42}) == "42"


@dataclass
class _DomainEvent:
    event_name: str = "test.event"
    run_id: str = ""
    root_run_id: str = ""
    workflow_id: str = ""


class TestKafkaEventPublisherKeying:
    def test_dataclass_event_extracts_root_run_id_as_key(self):
        client = MagicMock()
        pub = KafkaEventPublisher(client)
        pub.publish("topic.a", _DomainEvent(root_run_id="root-1", run_id="child-2"))

        client.publish_to_topic.assert_called_once()
        assert client.publish_to_topic.call_args.kwargs["key"] == "root-1"

    def test_dict_event_falls_back_to_run_id(self):
        client = MagicMock()
        pub = KafkaEventPublisher(client)
        pub.publish("topic.a", {"run_id": "r-1", "workflow_id": "wf-1"})

        assert client.publish_to_topic.call_args.kwargs["key"] == "r-1"

    def test_keyless_event_passes_none(self):
        client = MagicMock()
        pub = KafkaEventPublisher(client)
        pub.publish("topic.a", {"unrelated": "payload"})

        # None key means round-robin — acceptable for events with no
        # run/workflow affinity (catalog changes etc.).
        assert client.publish_to_topic.call_args.kwargs["key"] is None

    def test_non_dict_non_dataclass_event_does_not_key(self):
        client = MagicMock()
        pub = KafkaEventPublisher(client)
        pub.publish("topic.a", "raw-string-event")

        # payload becomes {"data": "..."} — no run/workflow fields,
        # so key is None. Verifies we don't crash on exotic input.
        assert client.publish_to_topic.call_args.kwargs["key"] is None
