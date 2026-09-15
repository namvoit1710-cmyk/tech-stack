"""Unit tests for the EventCollector module.

The collector is the contract that the orchestration engine's
``publish_event`` / ``publish_events`` / ``emit`` shims rely on to
preserve event order. Regressions here would surface as out-of-order
events on the wire (e.g. ``input.requested`` arriving before
``node.dispatched`` for the same task).
"""

from __future__ import annotations

import pytest

from app.layer1_domain.events import RunStatusChanged
from app.layer2_application.events.event_collector import (
    EventCollector,
    EventPriority,
    classify_priority,
)


class TestClassifyPriority:
    @pytest.mark.parametrize("topic", [
        "node.dispatched",
        "input.requested",
        "child_run.started",
    ])
    def test_immediate_topics(self, topic):
        assert classify_priority(topic) == EventPriority.IMMEDIATE

    @pytest.mark.parametrize("status", [
        "completed", "failed", "cancelled",
    ])
    def test_terminal_topics(self, status):
        event = RunStatusChanged(run_id="r", status=status)
        assert classify_priority("run.status", event) == EventPriority.TERMINAL

    @pytest.mark.parametrize("topic", [
        "node.completed", "edge.traversed",
        "context.updated", "inputrequested",  # class-name form falls to NORMAL
    ])
    def test_normal_topics(self, topic):
        assert classify_priority(topic) == EventPriority.NORMAL


class TestDrainOrdering:
    def test_immediate_before_normal_before_terminal(self):
        """Lanes drain in priority order regardless of insertion order."""
        c = EventCollector()
        c.append("run.status", RunStatusChanged(run_id="r", status="completed"))
        c.append("node.completed", "normal-event")
        c.append("node.dispatched", "immediate-event")

        drained = c.drain()
        assert [e.topic for e in drained] == [
            "node.dispatched",     # IMMEDIATE
            "node.completed",      # NORMAL
            "run.status",          # TERMINAL
        ]

    def test_insertion_order_preserved_within_same_lane(self):
        """Within a priority lane, drain order matches insertion order.

        This is the property that lets the engine guarantee
        ``node.dispatched`` reaches the wire before ``child_run.started``
        and ``input.requested`` when they all share the IMMEDIATE lane.
        """
        c = EventCollector()
        c.append("node.dispatched", "parent")
        c.append("child_run.started", "child")
        c.append("node.dispatched", "child-input-task")
        c.append("input.requested", "child-input-event")

        drained = c.drain()
        assert [(e.topic, e.event) for e in drained] == [
            ("node.dispatched", "parent"),
            ("child_run.started", "child"),
            ("node.dispatched", "child-input-task"),
            ("input.requested", "child-input-event"),
        ]

    def test_interleaved_lanes_keep_per_lane_insertion_order(self):
        """When IMMEDIATE/NORMAL events are interleaved at insertion,
        each lane preserves its own insertion order on drain.
        """
        c = EventCollector()
        term_event = RunStatusChanged(run_id="r", status="failed")
        c.append("node.dispatched", "imm-0")
        c.append("edge.traversed", "norm-0")
        c.append("child_run.started", "imm-1")
        c.append("node.completed", "norm-1")
        c.append("run.status", term_event)

        drained = c.drain()
        assert [e.event for e in drained] == [
            "imm-0",     # IMMEDIATE seq 0
            "imm-1",     # IMMEDIATE seq 2
            "norm-0",    # NORMAL seq 1
            "norm-1",    # NORMAL seq 3
            term_event,  # TERMINAL seq 4
        ]

    def test_drain_clears_collector(self):
        c = EventCollector()
        c.append("node.dispatched", "x")
        assert len(c) == 1
        c.drain()
        assert len(c) == 0
        # Second drain yields nothing
        assert c.drain() == []

    def test_explicit_priority_overrides_classification(self):
        """Callers can override the auto-classified priority — useful
        when emitting a topic that the classifier doesn't recognise but
        still needs to be in the IMMEDIATE lane.
        """
        c = EventCollector()
        # "inputrequested" (class-name form) classifies as NORMAL by default
        c.append("inputrequested", "imm", priority=EventPriority.IMMEDIATE)
        c.append("node.dispatched", "also-imm")

        drained = c.drain()
        assert [e.event for e in drained] == ["imm", "also-imm"]
