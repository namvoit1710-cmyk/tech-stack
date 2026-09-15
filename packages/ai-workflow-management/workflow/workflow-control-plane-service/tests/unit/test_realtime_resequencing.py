"""Tests for per-run event ordering: publisher seq stamping.

Covers the publisher half of the fix for "event order is not correct and not
consistent per run": ``RealtimeBroadcastingPublisher`` stamps a contiguous
``_run_seq`` per run, which is what lets a consumer detect gaps at all.

The consumer half moved with the sink. The Socket.IO consumer was removed once
every environment delivered realtime through the Push Gateway; its reorder
buffer lives on in ``PushGatewayRealtimeConsumer`` and is proved by
``test_push_gateway_consumer.py`` and ``test_two_cp_realtime_ordering.py``.
"""
from __future__ import annotations

import time

from app.layer4_frameworks.realtime.broadcasting_publisher import (
    REALTIME_TOPIC,
    RealtimeBroadcastingPublisher,
)


# --------------------------------------------------------------------------
# Publisher: per-run sequence stamping
# --------------------------------------------------------------------------


class _FakeBroker:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish_to_topic(self, topic: str, envelope: dict, key=None) -> None:
        assert topic == REALTIME_TOPIC
        self.published.append(envelope)


class TestPublisherRunSeq:
    def test_stamps_contiguous_run_seq_per_run(self):
        broker = _FakeBroker()
        pub = RealtimeBroadcastingPublisher(inner_publisher=None, broker=broker)

        for _ in range(3):
            pub.publish("node.completed", {"run_id": "r1"})

        seqs = [e["_run_seq"] for e in broker.published]
        assert seqs == [1, 2, 3]
        assert all(e["_run_key"] == "r1" for e in broker.published)

    def test_separate_runs_get_independent_counters(self):
        broker = _FakeBroker()
        pub = RealtimeBroadcastingPublisher(inner_publisher=None, broker=broker)

        pub.publish("node.completed", {"run_id": "r1"})
        pub.publish("node.completed", {"run_id": "r2"})
        pub.publish("node.completed", {"run_id": "r1"})

        by_run = [(e["_run_key"], e["_run_seq"]) for e in broker.published]
        assert by_run == [("r1", 1), ("r2", 1), ("r1", 2)]

    def test_nested_run_shares_root_partition_and_counter(self):
        broker = _FakeBroker()
        pub = RealtimeBroadcastingPublisher(inner_publisher=None, broker=broker)

        pub.publish("node.completed", {"run_id": "root", "root_run_id": "root"})
        pub.publish("node.completed", {"run_id": "child", "root_run_id": "root"})

        # Both keyed by the root run, so the counter is shared/contiguous.
        assert [(e["_run_key"], e["_run_seq"]) for e in broker.published] == [
            ("root", 1),
            ("root", 2),
        ]

    def test_counter_released_when_root_run_terminates(self):
        broker = _FakeBroker()
        pub = RealtimeBroadcastingPublisher(inner_publisher=None, broker=broker)

        pub.publish("node.completed", {"run_id": "r1"})                          # seq 1
        pub.publish("run.status", {"run_id": "r1", "status": "completed"})       # seq 2, then released
        pub.publish("node.completed", {"run_id": "r1"})                          # seq 1 again

        assert [e["_run_seq"] for e in broker.published] == [1, 2, 1]


# --------------------------------------------------------------------------
# Consumer: resequencing
# --------------------------------------------------------------------------
