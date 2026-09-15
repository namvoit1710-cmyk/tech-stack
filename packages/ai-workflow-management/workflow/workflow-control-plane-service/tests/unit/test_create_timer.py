"""SA-1371 S5 — CreateTimerUseCase.

Deterministic timer_id from (run_id, node_id, kind); idempotent create
(second create overwrites/updates fire_at, single store row); saves to
timer_repo + publishes TimerScheduled.
"""

from __future__ import annotations

import logging

import pytest

from app.layer1_domain.events import EventTopic, TimerScheduled
from app.layer2_application.features.create_timer.use_cases.create_timer_use_case import (
    CreateTimerCommand,
    CreateTimerUseCase,
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
    uc = CreateTimerUseCase(
        logger=logging.getLogger("test"),
        event_publisher=publisher,
        timer_repo=repo,
    )
    return repo, publisher, uc


@pytest.mark.asyncio
class TestCreateTimer:
    async def test_saves_and_publishes_scheduled(self):
        repo, publisher, uc = _build()
        result = await uc.execute(CreateTimerCommand(
            run_id="r1", workflow_id="wf1", node_id="n1", kind="delay",
            fire_at="2026-07-01T00:05:00Z",
        ))

        assert result.scheduled is True
        # Saved to the store under the derived id.
        stored = repo.get(result.timer_id)
        assert stored is not None
        assert stored.run_id == "r1"
        assert stored.node_id == "n1"
        assert stored.fire_at == "2026-07-01T00:05:00Z"
        # Published TimerScheduled.
        assert len(publisher.published) == 1
        topic, event = publisher.published[0]
        assert topic == str(EventTopic.TIMER_SCHEDULED)
        assert isinstance(event, TimerScheduled)
        assert event.timer_id == result.timer_id
        assert event.fire_at == "2026-07-01T00:05:00Z"

    async def test_timer_id_deterministic_from_run_node_kind(self):
        _, _, uc = _build()
        r1 = await uc.execute(CreateTimerCommand(
            run_id="r1", node_id="n1", kind="delay", fire_at="x"))
        r2 = await uc.execute(CreateTimerCommand(
            run_id="r1", node_id="n1", kind="delay", fire_at="y"))
        # Same (run, node, kind) → same id.
        assert r1.timer_id == r2.timer_id
        # Different kind → different id.
        r3 = await uc.execute(CreateTimerCommand(
            run_id="r1", node_id="n1", kind="sla", fire_at="x"))
        assert r3.timer_id != r1.timer_id

    async def test_idempotent_create_single_row_updates_fire_at(self):
        repo, publisher, uc = _build()
        await uc.execute(CreateTimerCommand(
            run_id="r1", node_id="n1", kind="delay",
            fire_at="2026-07-01T00:05:00Z"))
        r2 = await uc.execute(CreateTimerCommand(
            run_id="r1", node_id="n1", kind="delay",
            fire_at="2026-07-01T09:00:00Z"))

        # One store row (idempotent upsert), fire_at updated to latest.
        assert repo.list_by_run("r1") == [repo.get(r2.timer_id)]
        assert repo.get(r2.timer_id).fire_at == "2026-07-01T09:00:00Z"
