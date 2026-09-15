"""Append-only event log — Phase 4b foundation.

The event log is the **write-ahead** record of every domain event emitted by
the engine. Later phases build on it:

- **Phase 4b.2 (Dispatcher extraction):** downstream events carry a
  ``causation_id`` pointing back to the parent event, and the Dispatcher uses
  deterministic ids so redeliveries dedupe at the log.
- **Phase 4b.3 (Projector):** the Projector replays envelopes through a
  pure reducer to rebuild ``RunContextSnapshot``; the projector rebuild test
  wipes the run repo, replays, and asserts the result matches.
- **Phase 5 (Event-driven BUILTIN):** becomes the source of truth — the
  mutable ``WorkflowRun`` aggregate becomes a pure derivative of the log.

Scope of Phase 4b.1 (this drop): log every event as it is published,
keyed per-run, with stable ordering. Reads are offered via
``list_by_run`` so tests and later phases can inspect / replay. No
production read path consumes the log yet — it is a shadow write.

Invariants:
- Envelopes are frozen (``frozen=True``). The repo assigns ``sequence``
  at append time by returning a replaced copy.
- Dedupe is by ``(run_id, event_id)``. The same envelope appended twice
  is a no-op; ``append`` returns ``False`` on a duplicate.
- Order is the order of successful ``append`` calls per run.
"""

from __future__ import annotations

from typing import Protocol

# ``EventLogEnvelope`` is a pure domain value object — it lives in layer1 so the
# pure projector reducer can consume it without importing this application port.
# Re-exported here for the many call sites that build / read envelopes.
from app.layer1_domain.event_log import EventLogEnvelope  # noqa: F401


class IEventLogRepository(Protocol):
    """Per-run append-only log of domain events."""

    def append(self, envelope: EventLogEnvelope) -> bool:
        """Persist ``envelope`` under its ``run_id``.

        Returns ``True`` if the envelope was inserted, ``False`` if an
        envelope with the same ``event_id`` already exists for that run
        (dedupe). An empty ``run_id`` is allowed but goes to a dedicated
        "unscoped" bucket — callers should prefer an explicit id.
        """

    def list_by_run(self, run_id: str) -> list[EventLogEnvelope]:
        """Return all envelopes logged for ``run_id`` in append order.

        The returned envelopes carry the ``sequence`` assigned at insert
        time. Returns an empty list if the run has no logged events.
        """

    def list_by_run_since(
        self, run_id: str, min_sequence: int,
    ) -> list[EventLogEnvelope]:
        """Return envelopes logged for ``run_id`` with ``sequence`` strictly greater
        than ``min_sequence``, in append order.

        Introduced in Phase 4c.2 for the cached projector view store —
        lets the cache fetch only events it hasn't seen yet, giving
        O(Δ) per-get latency instead of O(|events|) full scans.

        Backends with an ordered index on ``sequence`` (SQLite's
        ``PRIMARY KEY (run_id, sequence)``) can resolve this in O(log n)
        index seek + O(Δ) row fetch. The memory backend filters its
        per-run list in O(|events_in_run|), which is still O(1) amortised
        for the "cache warm, no new events" hot path.
        """

    def count_by_run(self, run_id: str) -> int:
        """Return the number of envelopes logged for ``run_id``.

        Introduced for the replay debugger's paged timeline — lets a
        consumer learn the log size without materialising the rows.
        """

    def list_by_run_page(
        self, run_id: str, offset: int, limit: int,
    ) -> list[EventLogEnvelope]:
        """Return up to ``limit`` envelopes starting at 0-based position
        ``offset``, in append order.

        Because ``sequence`` is contiguous per run (monotonic counter,
        assigned at append; dedupe rejections consume no sequence),
        position ``i`` holds the envelope with ``sequence == i + 1``.
        Indexed backends resolve this as a ``(run_id, sequence)`` range
        seek — ``sequence > offset AND sequence <= offset + limit`` —
        with no OFFSET row-scan penalty on large logs.
        """

    def clear(self, run_id: str) -> int:
        """Drop every envelope for ``run_id``.

        Returns the number of envelopes removed. Intended for test
        cleanup and for the Projector rebuild path (Phase 4b.3 will
        use this to wipe-then-replay).
        """
