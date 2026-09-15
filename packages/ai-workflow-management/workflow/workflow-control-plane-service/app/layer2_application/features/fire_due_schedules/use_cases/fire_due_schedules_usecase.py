"""Schedule (crontab) trigger scheduler.

Fires workflow runs for TRIGGER nodes configured with
``config.trigger_type == "schedule"`` and a ``config.schedule_cron`` expression.
Driven by a periodic tick from the FastAPI lifespan (see ``main.py``); each tick
calls :meth:`FireDueSchedulesUseCase.execute`.

``now_epoch`` is injected so the due-check is deterministically testable.

Multi-instance note: firing creates a run and is NOT idempotent, so this is
gated behind ``WORKFLOW_SCHEDULER_ENABLED`` and must run on exactly one
control-plane instance. See docs/design/schedule-trigger-scheduler.md.
"""

import time
from typing import Any

from croniter import croniter

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
)
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)

_SCHEDULE = "schedule"


class FireDueSchedulesUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_repo: IWorkflowRepository,
        start_run: Any,
        monitor: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self._start_run = start_run
        self.monitor = monitor
        # (workflow_id, node_id) -> (cron_expr, next_fire_epoch)
        self._state: dict[tuple[str, str], tuple[str, float]] = {}
        # (workflow_id, node_id) for which we already logged a bad cron expr
        self._bad_cron_logged: set[tuple[str, str]] = set()

    async def execute(self, now_epoch: float | None = None) -> int:
        """Fire all schedules due at ``now``. Returns the number of runs started.

        Reads ``list_trigger_routes`` — **PUBLISHED main-flow rows only**. It
        used to read ``list_main_flows()``, which returns one row per VERSION
        with no status filter, so a cron expression from a version whose author
        had already removed it stayed live forever. This half was the more
        dangerous of the two: the event consumer at least dedupes on
        ``EventId.derive(event_id, workflow_id)``, but a schedule has no
        idempotency at all — a stale row fires a fresh run every boundary.
        See ``HanaWorkflowRepository.list_trigger_routes``.
        """
        now = now_epoch if now_epoch is not None else time.time()
        fired = 0
        for route in self.workflow_repo.list_trigger_routes((_SCHEDULE,)):
            cron = str(route.schedule_cron or "").strip()
            if not cron:
                continue

            key = (route.workflow_id, route.node_id)
            if not self._is_due(key, cron, now):
                continue

            if await self._fire(route.workflow_id, route.node_id, cron, now):
                fired += 1
        if self.monitor and fired:
            self.monitor.track("schedule_runs_fired", fired)
        return fired

    def _is_due(self, key: tuple[str, str], cron: str, now: float) -> bool:
        """True iff a cron boundary has elapsed since the last fire we acted on.

        On first sight (or when the cron expression changes) we baseline to the
        next boundary and do NOT fire — missed schedules across downtime are not
        backfilled, the safe default.
        """
        state = self._state.get(key)
        if state is None or state[0] != cron:
            next_fire = self._next(key, cron, now)
            if next_fire is not None:
                self._state[key] = (cron, next_fire)
            return False

        _, next_fire = state
        if now < next_fire:
            return False
        # Boundary passed. Reschedule from now (collapses multiple missed
        # boundaries into a single fire — no backfill storm).
        new_next = self._next(key, cron, now)
        if new_next is not None:
            self._state[key] = (cron, new_next)
        return True

    def _next(self, key: tuple[str, str], cron: str, now: float) -> float | None:
        """Next cron boundary strictly after ``now``; None if the expr is invalid."""
        try:
            return croniter(cron, now).get_next(float)
        except (ValueError, KeyError, TypeError) as exc:
            if key not in self._bad_cron_logged:
                self._bad_cron_logged.add(key)
                self.logger.warning(
                    "Skipping schedule with invalid cron expression",
                    workflow_id=key[0], node_id=key[1], cron=cron, error=str(exc),
                )
            return None

    async def _fire(self, workflow_id: str, node_id: str, cron: str, now: float) -> bool:
        try:
            result = await self._start_run.execute(
                StartRunCommand(
                    workflow_id=workflow_id,
                    input_data={},
                    tenant_id="system",
                    metadata={"trigger": _SCHEDULE, "schedule_cron": cron,
                              "trigger_node_id": node_id, "fired_at": now},
                )
            )
        except Exception as exc:  # noqa: BLE001 - a single bad run must not kill the loop
            self.logger.error(
                "Scheduled run failed to start",
                workflow_id=workflow_id, node_id=node_id, error=str(exc),
            )
            return False

        if getattr(result, "error", None):
            self.logger.error(
                "Scheduled run rejected",
                workflow_id=workflow_id, node_id=node_id, error=result.error,
            )
            return False

        self.logger.info(
            "Scheduled run started",
            workflow_id=workflow_id, node_id=node_id, cron=cron, run_id=result.id,
        )
        return True
