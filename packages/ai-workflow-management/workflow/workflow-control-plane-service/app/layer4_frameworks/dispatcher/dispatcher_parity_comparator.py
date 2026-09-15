"""Phase 2b — DispatcherParityComparator.

Records two event streams per run — the engine's inline routing
emissions and the DispatcherConsumer's shadow emissions — and
surfaces any divergence. The recorder wires both sides together
so tests and production runs can assert drift=0 before Phase 3a
flips the authoritative bit.

Compared quanta (all within one run_id):
- ``EdgeTraversed``: equality on ``(event_id, edge_id, source_port_id,
  target_node_id)``.
- ``NodeInput``: equality on ``(event_id, node_id)``.
- ``WireSkipped``: equality on ``(event_id, edge_id, reason)``.
- ``WireDeadLetter``: equality on ``(event_id, source_node_id)``.

A drift is recorded when one side observes an event tuple the other
side did not, or when the same ``event_id`` carries different payload
fields. Deterministic ids from ``compute_fanout`` guarantee that
byte-for-byte identical fan-out decisions produce byte-for-byte
identical tuples — any non-zero drift is a real behavioural divergence.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Iterable

from app.layer1_domain.events import EventTopic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftRecord:
    run_id: str
    kind: str  # "inline_only" | "dispatcher_only" | "payload_mismatch"
    topic: str
    event_id: str
    detail: str


class DispatcherParityRecorder:
    """Accumulates inline and dispatcher emissions per run and
    computes drift on finalize.

    Emissions arriving after ``finalize(run_id)`` are ignored — they
    belong to an already-settled run. This keeps the recorder's memory
    footprint bounded for long-lived processes.
    """

    def __init__(self, monitor: Any | None = None, ring_size: int = 100) -> None:
        self._inline: dict[str, dict[str, Any]] = {}
        self._dispatcher: dict[str, dict[str, Any]] = {}
        self._finalized: set[str] = set()
        self._drifts: deque[DriftRecord] = deque(maxlen=ring_size)
        self._total: int = 0
        self._monitor = monitor

    # ----- recording -----

    def record_inline(self, run_id: str, topic: str, event: Any) -> None:
        if run_id in self._finalized:
            return
        if topic not in _COMPARED_TOPICS:
            return
        key = _event_key(topic, event)
        if key is None:
            return
        self._inline.setdefault(run_id, {})[key] = _event_payload(topic, event)

    def record_dispatcher(self, run_id: str, emissions: Iterable[Any]) -> None:
        if run_id in self._finalized:
            return
        for emission in emissions:
            topic = getattr(emission, "topic", None)
            event = getattr(emission, "event", None)
            if topic is None or event is None or topic not in _COMPARED_TOPICS:
                continue
            key = _event_key(topic, event)
            if key is None:
                continue
            self._dispatcher.setdefault(run_id, {})[key] = _event_payload(
                topic, event,
            )

    # ----- drift computation -----

    def finalize(self, run_id: str) -> list[DriftRecord]:
        """Compute drift for ``run_id`` and flag it as settled."""
        if run_id in self._finalized:
            return []
        self._finalized.add(run_id)
        inline = self._inline.pop(run_id, {})
        dispatcher = self._dispatcher.pop(run_id, {})
        drifts: list[DriftRecord] = []

        inline_keys = set(inline.keys())
        dispatcher_keys = set(dispatcher.keys())

        for key in inline_keys - dispatcher_keys:
            topic, event_id = key
            drifts.append(DriftRecord(
                run_id=run_id, kind="inline_only", topic=topic,
                event_id=event_id, detail="dispatcher did not emit",
            ))
        for key in dispatcher_keys - inline_keys:
            topic, event_id = key
            drifts.append(DriftRecord(
                run_id=run_id, kind="dispatcher_only", topic=topic,
                event_id=event_id, detail="inline did not emit",
            ))
        for key in inline_keys & dispatcher_keys:
            if inline[key] != dispatcher[key]:
                topic, event_id = key
                drifts.append(DriftRecord(
                    run_id=run_id, kind="payload_mismatch", topic=topic,
                    event_id=event_id,
                    detail=f"inline={inline[key]} dispatcher={dispatcher[key]}",
                ))

        for drift in drifts:
            self._drifts.append(drift)
            self._total += 1
            logger.warning(
                "DispatcherParity drift: run=%s topic=%s kind=%s event_id=%s detail=%s",
                drift.run_id, drift.topic, drift.kind, drift.event_id, drift.detail,
            )
            if self._monitor is not None:
                try:
                    self._monitor.increment(
                        "dispatcher_drift_total",
                        labels={"kind": drift.kind},
                    )
                except Exception:
                    pass

        return drifts

    # ----- inspection -----

    @property
    def total(self) -> int:
        return self._total

    def recent(self) -> list[DriftRecord]:
        return list(self._drifts)

    def reset(self) -> None:
        self._inline.clear()
        self._dispatcher.clear()
        self._finalized.clear()
        self._drifts.clear()
        self._total = 0


# ----- topic helpers -----

_COMPARED_TOPICS = frozenset({
    EventTopic.EDGE_TRAVERSED,
    EventTopic.NODE_INPUT,
    EventTopic.WIRE_SKIPPED,
    EventTopic.WIRE_DEAD_LETTER,
})


def _event_key(topic: str, event: Any) -> tuple[str, str] | None:
    event_id = getattr(event, "event_id", "")
    if not event_id:
        return None
    return (topic, event_id)


def _event_payload(topic: str, event: Any) -> tuple:
    """Shape of the per-topic comparison tuple."""
    if topic == EventTopic.EDGE_TRAVERSED:
        return (
            getattr(event, "edge_id", ""),
            getattr(event, "source_node_id", ""),
            getattr(event, "target_node_id", ""),
            getattr(event, "source_port_id", ""),
        )
    if topic == EventTopic.NODE_INPUT:
        return (getattr(event, "node_id", ""),)
    if topic == EventTopic.WIRE_SKIPPED:
        return (
            getattr(event, "edge_id", ""),
            getattr(event, "reason", ""),
        )
    if topic == EventTopic.WIRE_DEAD_LETTER:
        return (
            getattr(event, "source_node_id", ""),
            tuple(getattr(event, "dropped_edge_ids", []) or []),
        )
    return ()


@dataclass
class DispatcherParityComparator:
    """Sugar wrapper that pairs an inline publisher hook with the
    recorder. A thin class so bootstrap can hold one reference and
    tests can inspect drift.
    """
    recorder: DispatcherParityRecorder = field(
        default_factory=DispatcherParityRecorder,
    )

    def record_inline(self, run_id: str, topic: str, event: Any) -> None:
        self.recorder.record_inline(run_id, topic, event)

    def finalize_run(self, run_id: str) -> list[DriftRecord]:
        return self.recorder.finalize(run_id)
