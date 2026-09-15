"""Timer-ownership lease port — SA-1371.

Abstracts *which pod fires timers*. Only the leaseholder ("leader") appends
``TimerFired`` — this is what prevents two pods from both scanning the store
and racing to fire the same timer. (Firing is already idempotent via the
deterministic ``event_id`` + the event log's ``(run_id, event_id)`` dedupe, so
a lost race is a safe no-op; the lease just keeps the common case single-owner
and avoids wasted work.)

v1 ships ``SingleOwnerLease`` — a trivial always-leader implementation gated by
``TIMER_DISPATCHER_ENABLED`` (operators enable the dispatcher on exactly one
pod). This protocol is the seam a distributed lease (Redis SETNX, Kafka
consumer-group partition ownership, a DB advisory lock) plugs into later
(subtask 6, deferred) with no change to ``TimerDispatcher``.
"""

from __future__ import annotations

from typing import Protocol


class ITimerLease(Protocol):
    """Single-owner election for the timer dispatcher."""

    def try_acquire(self) -> bool:
        """Attempt to become the leader. Returns ``True`` if this holder now
        owns the lease (or already did), ``False`` otherwise."""

    def renew(self) -> bool:
        """Extend the current lease. Returns ``True`` if still held after the
        renewal, ``False`` if it lapsed / was lost to another holder."""

    def release(self) -> None:
        """Relinquish the lease (e.g. on graceful shutdown) so another holder
        can acquire it promptly."""

    def is_leader(self) -> bool:
        """Return ``True`` when this holder currently owns the lease and may
        fire timers. The ``TimerDispatcher`` gates every tick on this."""
