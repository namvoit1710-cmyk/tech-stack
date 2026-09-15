"""Test helper for capturing engine-emitted events.

Phase 3b.1 removed the aggregate write from
``OrchestrationEngine.apply_state_updates_as_files``; tests that
previously verified writes via ``run.context.variables`` now need to
inspect the emitted ``NodeOutputProduced`` event instead. This helper
provides a minimal capturing publisher and a lookup utility that
reconstructs the equivalent ``variables`` dict from captured events.
"""
from __future__ import annotations

from typing import Any

from app.layer1_domain.events import EventTopic, NodeOutputProduced


class CapturingEventPublisher:
    """Minimal ``IEventPublisher`` that records every ``publish`` call.

    Mirrors the contract used by ``OrchestrationEngine._publish_now``:
    ``publish(topic, event)`` synchronous append-only record.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def publish(self, topic: str, event: Any) -> None:
        self.events.append((topic, event))

    def node_output_events(self) -> list[NodeOutputProduced]:
        return [
            evt for topic, evt in self.events
            if topic == EventTopic.NODE_OUTPUT_PRODUCED.value
            or isinstance(evt, NodeOutputProduced)
        ]

    def projected_variables(self) -> dict[str, Any]:
        """Rebuild ``variables`` from captured ``NodeOutputProduced`` events.

        Same reducer the projector applies: later writes clobber earlier
        ones, alias keys included. Lets a test assert the write-set that
        a projector-authoritative view would observe.
        """
        projected: dict[str, Any] = {}
        for evt in self.node_output_events():
            for k, v in (evt.state_updates or {}).items():
                projected[k] = v
        return projected
