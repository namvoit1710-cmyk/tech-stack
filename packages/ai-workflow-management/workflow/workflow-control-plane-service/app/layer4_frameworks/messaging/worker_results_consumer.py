"""Consumes task results published by the Worker Execution Service.

The executor publishes ``task.completed`` / ``task.failed`` messages to the
``workflow.results`` topic (see ``EventMeshResultPublisher``). This consumer
subscribes to that topic and drives the existing complete_task / fail_task
use cases — replacing the synchronous HTTP ``/complete`` callback.

Mirrors ``BrokerOrchestrationQueue``: the broker poll thread invokes the
callback, which bridges the work onto the captured main event loop so the
use cases run in the same async context as the rest of WCP.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand,
)
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import (
    FailTaskCommand,
)

logger = logging.getLogger(__name__)

RESULTS_TOPIC = "workflow.results"


class WorkerResultsConsumer:
    def __init__(
        self,
        container: dict,
        broker: Any,
        *,
        handler_timeout_s: float = 600.0,
    ) -> None:
        self._container = container
        self._broker = broker
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribed = False
        self._handler_timeout_s = handler_timeout_s

    def start(self) -> None:
        """Subscribe to the results topic. Call from the main asyncio thread."""
        if self._subscribed:
            return
        self._subscribed = True
        self._loop = asyncio.get_running_loop()

        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(RESULTS_TOPIC)
            except Exception:
                logger.warning("Could not auto-create topic '%s'", RESULTS_TOPIC)

        self._broker.subscribe_to_topic(RESULTS_TOPIC, self._on_message)
        logger.info("Worker results consumer subscribed to topic: %s", RESULTS_TOPIC)

    def _on_message(self, message: dict[str, Any]) -> None:
        if self._loop is None or self._loop.is_closed():
            logger.warning("Main event loop unavailable — dropping result: %s", message)
            return
        # Run the async handler and WAIT for it so the broker only acks/commits
        # after processing succeeds. Exceptions deliberately PROPAGATE to the
        # broker poll loop, which skips the ack/commit → the message is
        # redelivered (at-least-once). Swallowing here would ack a failed
        # completion and lose it (run hangs). Terminal/unprocessable messages are
        # short-circuited inside ``_handle`` (early return, no raise), so they ack
        # normally and don't redeliver. Safe to redeliver because complete_task/
        # fail_task are re-drive-on-replay (option 1): they have no terminal-status
        # short-circuit, and their side effects are idempotent (deterministic
        # ``NodeOutputProduced`` dedupe, ``completed_nodes`` frozenset fold,
        # edge-deduped dispatch), so a second delivery re-runs harmlessly.
        #
        # The wait is BOUNDED. It used to be a bare ``future.result()``, and
        # this consumer registers a *sync* callback, so the broker's own
        # ``fut.result(timeout=...)`` guard never applied to it either — the
        # broker takes the ``else: handler(payload)`` branch. One wedged
        # ``complete_task`` (a HANA lock, a hung file-service call) therefore
        # pinned this poll thread for the life of the pod: every
        # ``task.completed`` stopped arriving and every run in the tenant
        # stalled, with nothing logged and no metric moving.
        #
        # On timeout the coroutine is orphaned — it keeps running — and the
        # message is redelivered. That is a real cost, so it is logged loudly
        # and metered: a repeating ``worker_result_handler_timeout`` means a
        # handler is stuck and each redelivery leaks one more coroutine.
        # Better a visible retry than an invisible death.
        future = asyncio.run_coroutine_threadsafe(self._handle(message), self._loop)
        try:
            future.result(timeout=self._handler_timeout_s)
        except FuturesTimeoutError:
            payload = message.get("payload", {}) or {}
            logger.error(
                "Worker result handler exceeded %.0fs — orphaning it and "
                "letting the broker redeliver (run_id=%s task_id=%s type=%s). "
                "If this repeats, a handler is stuck: each redelivery leaks "
                "the previous coroutine.",
                self._handler_timeout_s,
                payload.get("run_id", ""),
                payload.get("task_id", ""),
                message.get("message_type", ""),
            )
            self._metric("timeout")
            raise

    def _metric(self, outcome: str) -> None:
        monitor = self._container.get("monitor")
        if monitor is not None:
            try:
                monitor.increment(
                    "worker_result_handler_timeout", tags={"outcome": outcome},
                )
            except Exception:  # noqa: BLE001 - metrics must never break consumption
                pass

    @staticmethod
    def _as_int(value: Any) -> int:
        """Coerce a wire value to a non-negative int, tolerating garbage.

        This rides an untrusted envelope: a worker that reports ``"1250"`` or
        ``null`` or nonsense must not break a completion. A bad duration costs
        a measurement; a raised exception costs the run.
        """
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    async def _handle(self, message: dict[str, Any]) -> None:
        message_type = message.get("message_type", "")
        payload = message.get("payload", {}) or {}
        run_id = payload.get("run_id") or message.get("correlation_id", "")
        task_id = payload.get("task_id", "")

        if not run_id or not task_id:
            logger.warning("Result message missing run_id/task_id: %s", message)
            return

        if message_type == "task.completed":
            uc = self._container.get("complete_task")
            if uc is None:
                logger.error("complete_task use case not available in container")
                return
            await uc.execute(CompleteTaskCommand(
                run_id=run_id,
                task_id=task_id,
                output_reference=payload.get("output_reference", "") or "",
                output_metadata=payload.get("output_metadata") or {},
                # Every executor completion publisher already sends this; the
                # control plane used to drop it here, which is why the worker's
                # own runtime was invisible in the event log.
                execution_time_ms=self._as_int(payload.get("execution_time_ms")),
            ))
        elif message_type == "task.failed":
            uc = self._container.get("fail_task")
            if uc is None:
                logger.error("fail_task use case not available in container")
                return
            await uc.execute(FailTaskCommand(
                run_id=run_id,
                task_id=task_id,
                error=payload.get("error") or {},
                outputs=payload.get("outputs") or payload.get("output_metadata") or {},
            ))
        else:
            logger.debug("Ignoring result message_type=%s", message_type)
