"""Sweep timed-out pending tasks: report failure and release concurrency."""

from __future__ import annotations

from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.pending_task_store_interface import IPendingTaskStore
from app.layer2_application.interfaces.result_reporter_interface import IResultReporter
from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher
from app.layer2_application.services.concurrency_control import ConcurrencyControl


class SweepTimedOutTasksUseCase:
    def __init__(
        self,
        logger: ILogger,
        pending_task_store: IPendingTaskStore,
        result_reporter: IResultReporter | None = None,
        result_publisher: IResultPublisher | None = None,
        concurrency_control: ConcurrencyControl | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.pending_task_store = pending_task_store
        self.result_reporter = result_reporter
        self.result_publisher = result_publisher
        self._concurrency = concurrency_control

    def execute(self) -> dict:
        entries = self.pending_task_store.get_timed_out()
        if not entries:
            return {"swept": 0}

        self.logger.info("Sweeping timed-out tasks", count=len(entries))

        swept = 0
        for entry in entries:
            # ``get_timed_out()`` and ``remove()`` are not atomic as a pair: the
            # callback may complete this task on another thread in between. Only
            # release + report if WE removed it — otherwise the callback already
            # released the slot and reported, and a second release/report here
            # would corrupt the concurrency counter and double-fail the task.
            if self.pending_task_store.remove(entry.task_id) is None:
                continue
            swept += 1

            # SA-1951 — an entry restored by ``_publish_or_fail`` already had its
            # slot released by the callback that could not deliver the result.
            # Releasing again here would inflate the in-flight budget.
            if self._concurrency and not entry.concurrency_released:
                self._concurrency.release()

            error_info = {
                "code": "WORKER_TIMEOUT",
                "message": f"Worker {entry.worker_type} did not callback in time",
                "retryable": True,
            }

            if self.result_reporter and entry.run_id:
                try:
                    self.result_reporter.report_failed(
                        run_id=entry.run_id,
                        task_id=entry.task_id,
                        error=error_info,
                    )
                except Exception as exc:
                    self.logger.error(
                        "Failed to report timeout to WCP",
                        task_id=entry.task_id,
                        error=str(exc),
                    )

            if self.result_publisher:
                self.result_publisher.publish_result({
                    "message_type": "task.failed",
                    "correlation_id": entry.run_id,
                    "payload": {
                        "task_id": entry.task_id,
                        "run_id": entry.run_id,
                        "node_id": entry.node_id,
                        "status": "FAILED",
                        "error": error_info,
                    },
                })

        return {"swept": swept}
