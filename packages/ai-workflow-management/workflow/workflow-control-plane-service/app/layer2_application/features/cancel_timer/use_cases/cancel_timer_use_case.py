"""Cancel a pending durable timer (SA-1371).

Deletes the timer from the ``ITimerRepository`` and publishes ``TimerCancelled``
so the read-side ``pending_timers`` view retires it. Called when a timer is
superseded, its node is skipped, or its run is cancelled.

Idempotent: cancelling an already-gone timer is a no-op delete; the
deterministic ``TimerCancelled.event_id`` dedupes at the event log so a
redelivered cancel folds to the same envelope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.events import EventTopic, TimerCancelled
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.event_publisher_interface import (
    IEventPublisher,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.timer_repository_interface import (
    ITimerRepository,
)


@dataclass
class CancelTimerCommand:
    run_id: str = ""
    node_id: str = ""
    timer_id: str = ""
    reason: str = ""


@dataclass
class CancelTimerResult:
    timer_id: str = ""
    run_id: str = ""
    cancelled: bool = False
    reason: str = ""
    error: str | None = None


class CancelTimerUseCase:
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

    async def execute(self, command: CancelTimerCommand) -> CancelTimerResult:
        # Idempotent — False when the timer was already fired / cancelled.
        removed = self.timer_repo.delete(command.timer_id)

        event = TimerCancelled(
            event_id=EventId.derive(
                command.run_id, f"timer_cancelled:{command.timer_id}",
            ),
            run_id=command.run_id,
            node_id=command.node_id,
            timer_id=command.timer_id,
            reason=command.reason,
        )
        self.event_publisher.publish(str(EventTopic.TIMER_CANCELLED), event)

        return CancelTimerResult(
            timer_id=command.timer_id,
            run_id=command.run_id,
            cancelled=removed,
            reason=command.reason,
        )
