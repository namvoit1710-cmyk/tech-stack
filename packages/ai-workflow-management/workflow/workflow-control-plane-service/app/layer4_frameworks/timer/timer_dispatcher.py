"""Timer dispatcher — SA-1371.

Polls ``ITimerRepository.list_due`` on a loop, and for each due timer publishes
a ``TimerFired`` event (via the injected ``event_publisher``) with a
**deterministic** ``event_id`` and then deletes the timer from the store. One
dispatcher per control-plane pod, gated on ``TIMER_DISPATCHER_ENABLED`` AND the
``ITimerLease.is_leader()`` check — only the single leader fires.

Idempotency
===========

The ``TimerFired`` event id is derived from ``(run_id, timer_id)`` via
``EventId.derive`` — stable across ticks, pods and redeliveries. The event log
dedupes on ``(run_id, event_id)``, so a double-fire (two pods, a re-scan after
a crash between publish and delete) folds to a safe no-op at the log. The store
``delete`` is likewise idempotent (``False`` when the row is already gone).

Poll cadence & backoff (mirrors ``OutboxDispatcher``)
=====================================================

- 100 ms when the last tick fired at least one timer (catch-up mode).
- ``poll_interval_s`` (default 1.0 s) when the last tick was empty.
- Exponential backoff (with 20% jitter, capped ~30 s) on an unexpected error.

Testing
=======

``drain_once(now_iso)`` fires every currently-due timer once and returns the
count — deterministic, no loop, no clock dependency. ``run_forever`` wraps it
with the poll cadence and derives ``now`` from the injected clock (or wall
clock).
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.entities.timer import Timer
from app.layer1_domain.events import EventTopic, TimerFired
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.event_publisher_interface import (
    IEventPublisher,
)
from app.layer2_application.interfaces.timer_lease_interface import ITimerLease
from app.layer2_application.interfaces.timer_repository_interface import (
    ITimerRepository,
)


_logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with 20% jitter — caps at ~30 s."""
    base = min(30.0, (2.0 ** max(1, attempt)) * 0.1)
    jitter = base * 0.2 * random.random()
    return base + jitter


class TimerDispatcher:
    """Async loop that fires due durable timers.

    Created at startup (``bootstrap.py``), wired into the FastAPI lifespan as a
    background task and cancelled on shutdown. Safe to construct even when
    disabled — with ``lease.is_leader()`` ``False`` every drain is a no-op.
    """

    BATCH_LIMIT = 100

    def __init__(
        self,
        timer_repo: ITimerRepository,
        event_publisher: IEventPublisher,
        lease: ITimerLease,
        *,
        poll_interval_s: float = 1.0,
        clock: Any = None,
    ) -> None:
        self._timer_repo = timer_repo
        self._event_publisher = event_publisher
        self._lease = lease
        self._poll_interval_s = poll_interval_s
        self._clock = clock
        self._error_attempts = 0
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        """Main loop — runs until ``stop`` is called."""
        while not self._stopped.is_set():
            try:
                fired = self.drain_once(self._now())
                self._error_attempts = 0
                sleep_s = 0.1 if fired > 0 else self._poll_interval_s
            except Exception:  # pragma: no cover — defensive
                _logger.exception("timer dispatcher iteration failed")
                self._error_attempts += 1
                sleep_s = _backoff_seconds(self._error_attempts)
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=sleep_s)
            except asyncio.TimeoutError:
                pass

    def drain_once(self, now_iso: str) -> int:
        """Fire every timer due at ``now_iso``. Returns the number fired.

        No-op (returns 0) when this pod does not hold the lease. Tests call
        this directly to avoid the ``run_forever`` loop.
        """
        if not self._lease.is_leader():
            return 0
        due = self._timer_repo.list_due(now_iso, limit=self.BATCH_LIMIT)
        fired = 0
        for timer in due:
            self._fire(timer)
            # Idempotent: a redelivered fire dedupes at the event log append;
            # the delete is a no-op when the row is already gone.
            self._timer_repo.delete(timer.timer_id)
            fired += 1
        return fired

    def stop(self) -> None:
        """Signal ``run_forever`` to exit at its next iteration."""
        self._stopped.set()

    # ─────────────────────── internal: publish TimerFired ──────────────────

    def _fire(self, timer: Timer) -> None:
        # ``EventId.derive`` gives a stable id from (run_id, timer_id) so a
        # re-fire (two pods, crash-recovery re-scan) collapses at the log
        # dedupe to the same envelope — TimerFired lands exactly once.
        event_id = EventId.derive(timer.run_id, f"timer_fired:{timer.timer_id}")
        event = TimerFired(
            event_id=event_id,
            run_id=timer.run_id,
            workflow_id=timer.workflow_id,
            node_id=timer.node_id,
            timer_id=timer.timer_id,
            kind=timer.kind,
            fire_at=timer.fire_at,
        )
        self._event_publisher.publish(str(EventTopic.TIMER_FIRED), event)

    def _now(self) -> str:
        if self._clock is not None:
            return self._clock()
        return _now_iso()
