"""Schedule a durable timer (SA-1371).

Persists a ``Timer`` to the ``ITimerRepository`` and publishes ``TimerScheduled``
so the read-side ``pending_timers`` view folds it in. The leader-gated
``TimerDispatcher`` later scans the store and fires it.

Idempotency: ``timer_id`` is derived deterministically from
``(run_id, node_id, kind)`` via ``EventId.derive`` — creating the same timer
twice overwrites the prior row (updating ``fire_at``) rather than duplicating
it, and the deterministic ``TimerScheduled.event_id`` dedupes at the event log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.entities.timer import Timer
from app.layer1_domain.events import EventTopic, TimerScheduled
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.event_publisher_interface import (
    IEventPublisher,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.timer_repository_interface import (
    ITimerRepository,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CreateTimerCommand:
    run_id: str = ""
    workflow_id: str = ""
    node_id: str = ""
    kind: str = "delay"
    fire_at: str = ""


@dataclass
class CreateTimerResult:
    timer_id: str = ""
    run_id: str = ""
    node_id: str = ""
    kind: str = ""
    fire_at: str = ""
    scheduled: bool = False
    error: str | None = None


class CreateTimerUseCase:
    def __init__(
        self,
        logger: ILogger,
        event_publisher: IEventPublisher,
        timer_repo: ITimerRepository,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.event_publisher = event_publisher
        self.timer_repo = timer_repo

    @staticmethod
    def derive_timer_id(run_id: str, node_id: str, kind: str) -> str:
        """Deterministic ``timer_id`` from ``(run_id, node_id, kind)``.

        Stable so a re-scheduled / replayed timer for the same node+kind maps
        to a single store row and a single ``TimerScheduled`` envelope.
        """
        return EventId.derive(run_id, f"timer:{node_id}:{kind}")

    async def execute(self, command: CreateTimerCommand) -> CreateTimerResult:
        timer_id = self.derive_timer_id(
            command.run_id, command.node_id, command.kind,
        )
        timer = Timer(
            timer_id=timer_id,
            run_id=command.run_id,
            workflow_id=command.workflow_id,
            node_id=command.node_id,
            kind=command.kind,
            fire_at=command.fire_at,
            created_at=_now_iso(),
        )
        # Idempotent upsert — same timer_id overwrites (updates fire_at).
        self.timer_repo.save(timer)

        event = TimerScheduled(
            event_id=EventId.derive(command.run_id, f"timer_scheduled:{timer_id}"),
            run_id=command.run_id,
            workflow_id=command.workflow_id,
            node_id=command.node_id,
            timer_id=timer_id,
            kind=command.kind,
            fire_at=command.fire_at,
        )
        self.event_publisher.publish(str(EventTopic.TIMER_SCHEDULED), event)

        return CreateTimerResult(
            timer_id=timer_id,
            run_id=command.run_id,
            node_id=command.node_id,
            kind=command.kind,
            fire_at=command.fire_at,
            scheduled=True,
        )
