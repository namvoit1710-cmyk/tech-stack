"""Durable timer entity (SA-1371).

A ``Timer`` is a persisted intent to fire a ``TimerFired`` event at ``fire_at``.
It is stored via ``ITimerRepository`` and scanned by the leader-gated
``TimerDispatcher``, which appends ``TimerFired`` when ``fire_at`` has elapsed.

``timer_id`` is deterministic (derived from run + node + kind + a sequence) so a
re-scheduled or replayed timer dedupes at the store and at the event log — the
timer fires exactly once even under redelivery / multi-instance races.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Timer:
    """A pending durable timer (immutable record)."""

    timer_id: str
    run_id: str
    workflow_id: str = ""
    node_id: str = ""
    kind: str = "delay"          # "delay" | "sla" | "schedule"
    fire_at: str = ""            # ISO-8601 wall-clock instant to fire at
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_due(self, now_iso: str) -> bool:
        """True when ``fire_at`` is at or before ``now_iso`` (ISO-8601 string
        comparison is valid for UTC ``Z`` timestamps of equal precision)."""
        return bool(self.fire_at) and self.fire_at <= now_iso
