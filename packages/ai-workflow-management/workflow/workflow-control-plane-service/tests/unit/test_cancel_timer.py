"""SA-1371 S5 — CancelTimerUseCase.

Deletes the timer from timer_repo + publishes TimerCancelled(reason).
Idempotent: cancelling an already-gone timer still publishes but reports
cancelled=False.
"""

from __future__ import annotations

import logging

import pytest

from app.layer1_domain.entities.timer import Timer
from app.layer1_domain.events import EventTopic, TimerCancelled
from app.layer2_application.features.cancel_timer.use_cases.cancel_timer_use_case import (
    CancelTimerCommand,
    CancelTimerUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_timer_repo import (
    MemoryTimerRepository,
)


class _FakePublisher:
    def __init__(self):
        self.published: list[tuple[str, object]] = []

    def publish(self, topic, event):
        self.published.append((topic, event))


def _build():
    repo = MemoryTimerRepository()
    publisher = _FakePublisher()
    uc = CancelTimerUseCase(
        logger=logging.getLogger("test"),
        event_publisher=publisher,
        timer_repo=repo,
    )
    return repo, publisher, uc


@pytest.mark.asyncio
class TestCancelTimer:
    async def test_deletes_and_publishes_cancelled(self):
        repo, publisher, uc = _build()
        repo.save(Timer(
            timer_id="t1", run_id="r1", node_id="n1",
            fire_at="2026-07-01T00:05:00Z"))

        result = await uc.execute(CancelTimerCommand(
            run_id="r1", node_id="n1", timer_id="t1", reason="node_skipped"))

        assert result.cancelled is True
        # Removed from the store.
        assert repo.get("t1") is None
        # Published TimerCancelled with the reason.
        assert len(publisher.published) == 1
        topic, event = publisher.published[0]
        assert topic == str(EventTopic.TIMER_CANCELLED)
        assert isinstance(event, TimerCancelled)
        assert event.timer_id == "t1"
        assert event.reason == "node_skipped"

    async def test_cancel_missing_is_idempotent_noop_delete(self):
        repo, publisher, uc = _build()
        result = await uc.execute(CancelTimerCommand(
            run_id="r1", node_id="n1", timer_id="ghost", reason="superseded"))

        # No such timer → delete reports False, but the cancel event still
        # publishes so the read-side view retires any stale entry.
        assert result.cancelled is False
        assert len(publisher.published) == 1
        assert publisher.published[0][1].reason == "superseded"
