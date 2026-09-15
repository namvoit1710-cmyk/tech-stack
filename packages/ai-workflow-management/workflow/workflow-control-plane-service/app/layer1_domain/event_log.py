"""Event-log envelope — one entry in the append-only domain event log.

A pure domain value object. The projector reducer
(``ProjectedRunState.apply``) folds these into ``ProjectedRunState``,
and the ``IEventLogRepository`` port (layer2) stores / returns them.
It lives in layer1 so the pure reducer can consume it without importing
an application port — the repository interface re-exports it for the
many call sites that build / read envelopes.

Invariants:
- Envelopes are frozen (``frozen=True``). The repo assigns ``sequence``
  at append time by returning a replaced copy.
- Dedupe is by ``(run_id, event_id)``. The same envelope appended twice
  is a no-op; ``append`` returns ``False`` on a duplicate.
- Order is the order of successful ``append`` calls per run.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EventLogEnvelope:
    """One entry in the event log.

    ``sequence`` is assigned by the repository at ``append`` time and is
    only meaningful on envelopes returned from ``list_by_run``. Callers
    building a draft envelope leave it at ``0``.
    """

    event_id: str
    run_id: str
    topic: str
    event_type: str
    timestamp: str
    payload: dict[str, Any]
    sequence: int = 0
    source: str = ""
    causation_id: str = ""
