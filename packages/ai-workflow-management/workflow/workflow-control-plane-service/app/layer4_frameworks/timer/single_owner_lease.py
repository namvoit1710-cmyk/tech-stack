"""Single-owner timer lease — SA-1371 v1.

The trivial ``ITimerLease`` implementation: when constructed ``enabled=True``
it is *always* the leader. Correct because operators enable the timer
dispatcher on exactly one pod (``TIMER_DISPATCHER_ENABLED``), so there is no
contention to arbitrate.

This is the seam a distributed lease (Redis SETNX, Kafka partition ownership, a
DB advisory lock) plugs into later — subtask 6, deferred — with no change to
``TimerDispatcher``, which only calls ``is_leader()`` each tick.

Constructed ``enabled=False`` it is never the leader, which lets the dispatcher
loop idle harmlessly even if it is somehow started with the feature gated off.
"""

from __future__ import annotations

from app.layer2_application.interfaces.timer_lease_interface import ITimerLease


class SingleOwnerLease(ITimerLease):
    """Always-leader (or never-leader) lease for the single-pod v1."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._enabled = bool(enabled)
        self._held = bool(enabled)

    def try_acquire(self) -> bool:
        if self._enabled:
            self._held = True
        return self._held

    def renew(self) -> bool:
        return self._held

    def release(self) -> None:
        self._held = False

    def is_leader(self) -> bool:
        return self._held
