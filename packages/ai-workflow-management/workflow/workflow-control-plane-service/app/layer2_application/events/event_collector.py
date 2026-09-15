"""Per-task event collector with priority-based drain ordering.

Events are classified into three priority lanes:
- IMMEDIATE: node.dispatched, input.requested, etc. (triggers workers/frontend)
- NORMAL: edge.traversed, node.completed (DAG lifecycle)
- TERMINAL: run.status with a terminal status (completed/failed/cancelled) (must arrive last)

Within each priority lane, events are ordered by insertion sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from app.layer1_domain.events import EventTopic


class EventPriority(IntEnum):
    IMMEDIATE = 0
    NORMAL = 1
    TERMINAL = 2


@dataclass
class EventEnvelope:
    topic: str
    event: Any
    priority: EventPriority
    sequence: int


# Only the canonical dotted EventTopic forms are classified into the
# IMMEDIATE / TERMINAL lanes. The class-name-lowercased form that
# ``publish_events`` emits (e.g. "nodedispatched") deliberately falls
# through to NORMAL — engine-emitted IMMEDIATE/TERMINAL events always
# use the explicit dotted EventTopic, never ``publish_events``.
#
# Run-terminal events all share the ``run.status`` topic, so the
# TERMINAL lane is decided by the event's ``status`` field rather than
# by topic alone.
_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled"})
_IMMEDIATE_TOPICS = frozenset({
    EventTopic.NODE_DISPATCHED,
    EventTopic.INPUT_REQUESTED,
    EventTopic.CHILD_RUN_STARTED,
})


def classify_priority(topic: str, event: Any = None) -> EventPriority:
    """Auto-classify a (topic, event) pair into a priority lane."""
    if topic == EventTopic.RUN_STATUS_CHANGED:
        status = str(getattr(event, "status", "") or "").lower()
        if status in _TERMINAL_RUN_STATUSES:
            return EventPriority.TERMINAL
        return EventPriority.NORMAL
    if topic in _IMMEDIATE_TOPICS:
        return EventPriority.IMMEDIATE
    return EventPriority.NORMAL


class EventCollector:
    """Collects events during a single orchestration pass, drains in priority order.

    Usage::

        collector = EventCollector()
        collector.append("node.dispatched", event1)
        collector.append("run.completed", event2)
        envelopes = collector.drain()  # IMMEDIATE first, TERMINAL last
    """

    def __init__(self) -> None:
        self._events: list[EventEnvelope] = []
        self._seq = 0

    def append(self, topic: str, event: Any, priority: EventPriority | None = None) -> None:
        if priority is None:
            priority = classify_priority(topic, event)
        self._events.append(EventEnvelope(
            topic=topic, event=event, priority=priority, sequence=self._seq,
        ))
        self._seq += 1

    def drain(self) -> list[EventEnvelope]:
        """Return events sorted by (priority, sequence) and reset the collector."""
        events = sorted(self._events, key=lambda e: (e.priority, e.sequence))
        self._events = []
        self._seq = 0
        return events

    def __len__(self) -> int:
        return len(self._events)
