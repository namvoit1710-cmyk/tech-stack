"""Sweep pull tasks (SA-2033, B8) — the pull fork of the timeout sweeper.

Reaps the two pull states the push sweeper doesn't (push DISPATCHED→fail stays in
SweepTimedOutTasksUseCase, untouched):
  - LEASED + lease expired → re-enqueue (attempt++, exponential backoff) and emit
    task.lease_expired; after max_lease_attempts → POISON: a non-retryable
    TASK_FAILED_POISON terminal + a DLQ/alert event (never re-driven into a void).
  - READY too long (no worker ever leased it within max-ready-TTL) → WORKER_UNAVAILABLE.

Runs on the same periodic loop as the push sweeper (main.py). All emission is via
result_publisher using the pull_lifecycle contract builders.
"""

from __future__ import annotations

from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.app_config_interface import IAppConfig
from app.layer2_application.interfaces.ready_task_store_interface import IReadyTaskStore
from app.layer2_application.services import pull_lifecycle


class SweepPullTasksUseCase:
    def __init__(
        self,
        logger: ILogger,
        ready_task_store: IReadyTaskStore | None = None,
        result_publisher: Any = None,
        result_reporter: Any = None,
        app_config: IAppConfig | None = None,
        monitor: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.ready_task_store = ready_task_store
        self._result_publisher = result_publisher
        self._result_reporter = result_reporter
        self._app_config = app_config
        self._monitor = monitor

    def execute(self) -> dict:
        if self.ready_task_store is None:
            return {"re_enqueued": 0, "poisoned": 0, "unavailable": 0}

        max_attempts = int(getattr(self._app_config, "MAX_LEASE_ATTEMPTS", 0) or 0)
        backoff = float(getattr(self._app_config, "LEASE_BACKOFF_BASE_SECONDS", 0) or 0)
        max_ready = int(getattr(self._app_config, "LEASE_MAX_READY_SECONDS", 0) or 0)

        result = self.ready_task_store.sweep_expired_leases(
            max_attempts=max_attempts, backoff_base_seconds=backoff,
        )
        for row in result.re_enqueued:
            self._emit(pull_lifecycle.lease_expired_event(row))
        for row in result.poisoned:
            self._poison(row)

        unavailable = []
        if max_ready:
            unavailable = self.ready_task_store.sweep_stale_ready(max_ready)
            for row in unavailable:
                self.logger.warning(
                    "Pull task WORKER_UNAVAILABLE (no lease before max-ready-TTL)",
                    task_id=row.task_id, worker_type=row.worker_type,
                )
                self._emit(pull_lifecycle.worker_unavailable_event(row))
                self._report_terminal(row, pull_lifecycle.WORKER_UNAVAILABLE,
                                      "no worker leased the task before max-ready-TTL")

        # B9 — refresh the per-partition queue-depth gauges on this periodic tick
        # (zeros drained partitions so KEDA can scale them back to zero).
        if self._monitor and hasattr(self._monitor, "refresh_pull_gauges"):
            try:
                self._monitor.refresh_pull_gauges(self.ready_task_store.stats_all())
            except Exception:
                pass

        n_re, n_poison, n_unavail = len(result.re_enqueued), len(result.poisoned), len(unavailable)
        if n_re or n_poison or n_unavail:
            self.logger.info(
                "Pull sweep",
                re_enqueued=n_re, poisoned=n_poison, unavailable=n_unavail,
            )
        return {"re_enqueued": n_re, "poisoned": n_poison, "unavailable": n_unavail}

    def _poison(self, row: Any) -> None:
        # Non-retryable terminal to WCP + DLQ/alert. Log at ERROR so the poison is
        # greppable regardless of the broker binding.
        self.logger.error(
            "Pull task POISONED (exhausted lease attempts)",
            task_id=row.task_id, run_id=row.run_id, attempt_count=row.attempt_count,
        )
        if self._monitor and hasattr(self._monitor, "increment"):
            try:
                self._monitor.increment("pull_task_poisoned")
            except Exception:
                pass
        self._emit(pull_lifecycle.poison_event(row))
        self._emit(pull_lifecycle.dlq_event(row))
        self._report_terminal(row, pull_lifecycle.TASK_FAILED_POISON,
                              "task exhausted its lease attempts (poison)")

    def _report_terminal(self, row: Any, code: str, message: str) -> None:
        """Report a NON-RETRYABLE terminal (poison / worker-unavailable) on the
        authoritative sync reporter path too, so an HTTP/gRPC-mode WCP records it —
        not only the event-mesh publish (same gap the B7 fence closed)."""
        if not (self._result_reporter and getattr(row, "run_id", "")):
            return
        try:
            self._result_reporter.report_failed(
                run_id=row.run_id, task_id=row.task_id,
                error={"code": code, "message": message, "retryable": False},
                run_generation=getattr(row, "run_generation", ""),
                idempotency_key=getattr(row, "idempotency_key", ""),
            )
        except Exception as exc:
            self.logger.warning("Failed to report terminal to WCP",
                                task_id=row.task_id, code=code, error=str(exc))

    def _emit(self, message: dict) -> None:
        if not self._result_publisher:
            return
        try:
            self._result_publisher.publish_result(message)
        except Exception as exc:
            self.logger.warning(
                "Failed to publish pull-sweep event",
                message_type=message.get("message_type"), error=str(exc),
            )
