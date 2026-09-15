"""Durable-timer repository port — SA-1371.

A keyed store of pending ``Timer`` records (see
``app.layer1_domain.entities.timer.Timer``). Unlike the append-only event log,
this is a mutable table keyed by ``timer_id``: a timer is ``save``-d when a
handler schedules it, ``list_due``-scanned by the leader-gated
``TimerDispatcher`` each tick, and ``delete``-d once it fires or is cancelled.

The store is a *convenience index* over the durable-timer intent — the event
log remains authoritative (``TimerScheduled`` / ``TimerFired`` /
``TimerCancelled`` reconstruct ``pending_timers`` on replay). Losing the store
only costs a rescan; it never loses a timer that has been logged.

Backends: ``MemoryTimerRepository`` (mock / tests), ``SqliteTimerRepository``
(local dev / CI), ``HanaTimerRepository`` (SAP BTP production) — mirroring the
event-log repository trio.
"""

from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.timer import Timer


class ITimerRepository(Protocol):
    """Keyed store of pending durable timers."""

    def save(self, timer: Timer) -> None:
        """Upsert ``timer`` keyed by ``timer_id``.

        Scheduling the same ``timer_id`` twice overwrites the prior record
        (idempotent re-schedule / ``fire_at`` update) rather than inserting a
        duplicate — the deterministic ``timer_id`` means a redelivered schedule
        collapses to one row.
        """

    def list_due(self, now_iso: str, limit: int = 100) -> list[Timer]:
        """Return pending timers whose ``fire_at`` is at or before ``now_iso``.

        ``fire_at <= now_iso`` (ISO-8601 string comparison is valid for UTC
        timestamps of equal precision). ``limit`` caps the batch so the
        dispatcher drains large backlogs across successive ticks.
        """

    def list_by_run(self, run_id: str) -> list[Timer]:
        """Return every pending timer for ``run_id`` (unordered)."""

    def delete(self, timer_id: str) -> bool:
        """Remove the timer with ``timer_id``.

        Returns ``True`` when a row was removed, ``False`` when no such timer
        existed — idempotent so a double-fire / double-cancel is a safe no-op.
        """

    def get(self, timer_id: str) -> Timer | None:
        """Return the timer with ``timer_id``, or ``None`` if absent."""
