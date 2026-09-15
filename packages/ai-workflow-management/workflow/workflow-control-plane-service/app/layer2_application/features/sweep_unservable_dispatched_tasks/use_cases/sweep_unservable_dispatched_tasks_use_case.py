"""Liveness sweep: fail dispatched TASK tasks whose worker_type has NO live
worker (Signal A). Opt-in backstop for the silent-hang class where a task is
dispatched to a worker type that no live worker serves — e.g. a whole worker
fleet is down/undeployed, or a mis-typed ``builtin:*`` slipped past the
dispatch guard.

Why liveness, not a wall-clock timeout: a duration timeout cannot tell a dead
worker from a legitimately long-running task. This sweep instead fails a task
ONLY when the worker registry reports zero live workers of its type — so a task
whose worker is alive (still heart-beating) is NEVER failed, no matter how long
it runs. Two safety layers guard against transient registry gaps:

  * empty-registry skip — if the registry reports zero live workers *at all*
    (a gRPC blip, or every worker mid-restart), the whole tick is skipped
    rather than failing every dispatched task;
  * per-task grace — a task is only swept once it has been RUNNING for at least
    ``WORKER_DISPATCH_LIVENESS_GRACE_SECONDS`` (tolerates a brief per-type gap
    during a rolling restart). The grace is a floor on age, NOT a ceiling — a
    long task with a *live* worker type is exempt by the liveness check above,
    so this never false-fails long work.

Gap (deferred to Signal B, per-task lease/heartbeat): a single worker instance
that dies mid-task while sibling instances of the same type stay alive is NOT
caught here — the type still reads "live". Closing that needs a per-task claim
+ lease renewal in the worker protocol.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.run_repository_interface import ListRunsFilter
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import (
    FailTaskCommand,
)


_ACTIVE_RUNS_PAGE_SIZE = 100


class SweepUnservableDispatchedTasksUseCase:
    def __init__(
        self,
        logger: ILogger,
        task_repo: Any,
        run_repo: Any,
        worker_registry_client: Any = None,
        app_config: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.task_repo = task_repo
        self.run_repo = run_repo
        self._registry = worker_registry_client
        self._config = app_config
        # Post-wired in ``build_app_container`` (another use case can't be
        # constructor-injected — it isn't built yet at discovery time).
        self._fail_task: Any = None

    async def execute(self) -> dict:
        if not getattr(self._config, "WORKER_DISPATCH_LIVENESS_SWEEP_ENABLED", False):
            return {"swept": 0, "enabled": False}
        if self._registry is None or self._fail_task is None:
            return {"swept": 0, "enabled": True, "reason": "not_wired"}

        grace_s = int(
            getattr(self._config, "WORKER_DISPATCH_LIVENESS_GRACE_SECONDS", 60) or 0
        )

        try:
            live_types = self._live_worker_types()
        except Exception as exc:
            self.logger.error(
                "Liveness sweep: worker registry unavailable — skipping tick",
                error=str(exc),
            )
            return {"swept": 0, "enabled": True, "reason": "registry_error"}

        # Safety valve: an empty snapshot (registry blip / every worker mid-
        # restart) would otherwise fail every dispatched task. Never sweep on
        # an empty registry — the per-type check below is only trustworthy when
        # the registry itself is reporting *some* live workers.
        if not live_types:
            self.logger.warning(
                "Liveness sweep: registry reported zero live workers — skipping tick",
            )
            return {"swept": 0, "enabled": True, "reason": "empty_registry"}

        now = datetime.now(timezone.utc)
        swept = 0
        for run in await self._list_active_runs():
            try:
                tasks = await self.task_repo.alist_by_run(run.id)
            except Exception as exc:
                self.logger.error(
                    "Liveness sweep: listing tasks failed — skipping run",
                    run_id=getattr(run, "id", "?"), error=str(exc),
                )
                continue
            for task in tasks:
                if not self._is_unservable_dispatched(task, live_types, now, grace_s):
                    continue
                try:
                    await self._fail_unservable_task(run, task)
                    swept += 1
                except Exception as exc:
                    self.logger.error(
                        "Liveness sweep: failed to fail unservable task",
                        task_id=getattr(task, "id", "?"), error=str(exc),
                    )

        if swept:
            self.logger.info(
                "Liveness sweep failed unservable dispatched tasks", count=swept,
            )
        return {"swept": swept, "enabled": True}

    # ------------------------------------------------------------------

    def _live_worker_types(self) -> set[str]:
        """Worker types with at least one registered (heart-beating) worker."""
        workers = self._registry.list_workers() or []
        return {
            w.get("worker_type")
            for w in workers
            if isinstance(w, dict) and w.get("worker_type")
        }

    def _is_unservable_dispatched(
        self, task: Any, live_types: set[str], now: datetime, grace_s: int,
    ) -> bool:
        if task.status != TaskStatus.RUNNING:
            return False
        worker_type = task.worker_type
        # Only external-worker dispatches. ``builtin:*`` are engine-inline; a
        # mis-typed one is handled fail-fast at dispatch (Layer 1), not here.
        if not worker_type or worker_type.startswith("builtin:"):
            return False
        # A live worker of this type exists → never fail (long tasks are safe).
        if worker_type in live_types:
            return False
        started = task.started_at or task.created_at
        elapsed_s = self._elapsed_s(started, now)
        return elapsed_s is not None and elapsed_s >= grace_s

    async def _list_active_runs(self) -> list[Any]:
        """Page through all RUNNING runs (any depth). Context-thin summaries —
        only ``id`` is used, tasks are re-fetched per run."""
        collected: list[Any] = []
        offset = 0
        while True:
            page = await self.run_repo.alist_runs(
                ListRunsFilter(
                    statuses=[RunStatus.RUNNING.value],
                    offset=offset,
                    limit=_ACTIVE_RUNS_PAGE_SIZE,
                )
            )
            collected.extend(page.items)
            offset += len(page.items)
            if not page.items or offset >= page.total:
                break
        return collected

    async def _fail_unservable_task(self, run: Any, task: Any) -> None:
        self.logger.info(
            "Liveness sweep failing unservable dispatched task",
            run_id=run.id, task_id=task.id, node_id=task.node_id,
            worker_type=task.worker_type,
        )
        await self._fail_task.execute(
            FailTaskCommand(
                run_id=run.id,
                task_id=task.id,
                error={
                    "code": "NO_LIVE_WORKER",
                    "message": (
                        f"No live worker registered for worker_type "
                        f"'{task.worker_type}' (node '{task.node_id}'). The task "
                        f"was dispatched but is unservable — failed by the "
                        f"liveness sweep."
                    ),
                },
            )
        )

    @staticmethod
    def _elapsed_s(started_iso: Optional[str], now: datetime) -> Optional[float]:
        if not started_iso:
            return None
        try:
            started = datetime.fromisoformat(started_iso)
        except (ValueError, TypeError):
            return None
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (now - started).total_seconds()
