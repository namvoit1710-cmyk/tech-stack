"""SA-1371 S3 — TimerDispatcher.drain_once semantics.

Drives the dispatcher with a MemoryTimerRepository, a fake event publisher
(captures published events) and an injected clock, asserting:
  - due timers fire (TimerFired published + timer deleted from the store),
  - not-yet-due timers are skipped,
  - a second drain is idempotent (no-op — the timer is already gone),
  - nothing fires when ``is_leader()`` is False.
"""

from __future__ import annotations

from app.layer1_domain.entities.timer import Timer
from app.layer1_domain.events import EventTopic, TimerFired
from app.layer4_frameworks.persistence.memory.memory_timer_repo import (
    MemoryTimerRepository,
)
from app.layer4_frameworks.timer.single_owner_lease import SingleOwnerLease
from app.layer4_frameworks.timer.timer_dispatcher import TimerDispatcher


# ────────────────────────── fakes ──────────────────────────


class _FakePublisher:
    def __init__(self):
        self.published: list[tuple[str, object]] = []

    def publish(self, topic, event):
        self.published.append((topic, event))


class _NotLeaderLease:
    def try_acquire(self):
        return False

    def renew(self):
        return False

    def release(self):
        return None

    def is_leader(self):
        return False


def _timer(timer_id: str, *, fire_at: str, run_id: str = "r1",
           node_id: str = "n1", kind: str = "delay") -> Timer:
    return Timer(
        timer_id=timer_id,
        run_id=run_id,
        workflow_id="wf1",
        node_id=node_id,
        kind=kind,
        fire_at=fire_at,
        created_at="2026-07-01T00:00:00Z",
    )


def _build(*, leader: bool = True):
    repo = MemoryTimerRepository()
    publisher = _FakePublisher()
    lease = SingleOwnerLease(enabled=leader)
    dispatcher = TimerDispatcher(
        timer_repo=repo, event_publisher=publisher, lease=lease,
    )
    return repo, publisher, dispatcher


class TestDrainOnce:
    def test_fires_due_timer_publishes_and_deletes(self):
        repo, publisher, dispatcher = _build()
        repo.save(_timer("t1", fire_at="2026-07-01T00:00:00Z"))

        fired = dispatcher.drain_once("2026-07-01T00:05:00Z")

        assert fired == 1
        assert len(publisher.published) == 1
        topic, event = publisher.published[0]
        assert topic == str(EventTopic.TIMER_FIRED)
        assert isinstance(event, TimerFired)
        assert event.timer_id == "t1"
        assert event.run_id == "r1"
        assert event.node_id == "n1"
        assert event.fire_at == "2026-07-01T00:00:00Z"
        # Timer retired from the store after firing.
        assert repo.get("t1") is None

    def test_skips_not_yet_due(self):
        repo, publisher, dispatcher = _build()
        repo.save(_timer("t1", fire_at="2026-07-01T09:00:00Z"))

        fired = dispatcher.drain_once("2026-07-01T00:05:00Z")

        assert fired == 0
        assert publisher.published == []
        assert repo.get("t1") is not None

    def test_idempotent_second_drain_noop(self):
        repo, publisher, dispatcher = _build()
        repo.save(_timer("t1", fire_at="2026-07-01T00:00:00Z"))

        assert dispatcher.drain_once("2026-07-01T00:05:00Z") == 1
        # Second drain — timer already fired + deleted → nothing to do.
        assert dispatcher.drain_once("2026-07-01T00:05:00Z") == 0
        assert len(publisher.published) == 1

    def test_deterministic_event_id_stable(self):
        repo, publisher, dispatcher = _build()
        repo.save(_timer("t1", fire_at="2026-07-01T00:00:00Z"))
        dispatcher.drain_once("2026-07-01T00:05:00Z")

        # Re-save the same timer and fire again → same deterministic event_id,
        # so the event log's (run_id, event_id) dedupe folds it to a no-op.
        repo.save(_timer("t1", fire_at="2026-07-01T00:00:00Z"))
        dispatcher.drain_once("2026-07-01T00:05:00Z")

        ids = {ev.event_id for _, ev in publisher.published}
        assert len(ids) == 1

    def test_not_leader_does_nothing(self):
        repo = MemoryTimerRepository()
        publisher = _FakePublisher()
        dispatcher = TimerDispatcher(
            timer_repo=repo, event_publisher=publisher, lease=_NotLeaderLease(),
        )
        repo.save(_timer("t1", fire_at="2026-07-01T00:00:00Z"))

        fired = dispatcher.drain_once("2026-07-01T00:05:00Z")

        assert fired == 0
        assert publisher.published == []
        # Not fired → still pending.
        assert repo.get("t1") is not None
