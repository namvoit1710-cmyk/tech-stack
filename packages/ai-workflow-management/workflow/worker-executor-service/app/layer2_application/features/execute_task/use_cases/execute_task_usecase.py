"""Execute task use case: dispatch task to worker, handle retry/concurrency, report to WCP."""

from __future__ import annotations

import time

import httpx
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from app.layer2_application.interfaces.app_config_interface import IAppConfig
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.task_queue_interface import ITaskQueue
from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher
from app.layer2_application.interfaces.decision_switch_client_interface import IDecisionSwitchClient
from app.layer2_application.interfaces.result_reporter_interface import IResultReporter
from app.layer2_application.interfaces.pending_task_store_interface import IPendingTaskStore, PendingTaskEntry


def _saturation_status(exc: Exception) -> int | None:
    """Return the HTTP status when *exc* is a worker saturation/unavailable
    acceptance response (SA-1531): 429 WORKER_SATURATED (SA-1530 gate) or 503.
    Backpressure, not failure — the caller REQUEUEs instead of failing."""
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        if exc.response.status_code in (429, 503):
            return exc.response.status_code
    return None
from app.layer1_domain.entities.execution_task import ExecutionTask
from app.layer1_domain.errors import InstancePinUnavailableError
from app.layer1_domain.value_objects.delivery_mode import DeliveryMode
from app.layer2_application.interfaces.ready_task_store_interface import (
    IReadyTaskStore, TaskRow, ENQUEUE_OK, ENQUEUE_ADMISSION_FULL,
)
from app.layer2_application.services import pull_lifecycle
from app.layer2_application.services.retry_policy import RetryPolicy
from app.layer2_application.services.concurrency_control import ConcurrencyControl


@dataclass
class ExecuteTaskInput:
    task_id: str = ""
    run_id: str = ""
    node_id: str = ""
    worker_type: str = ""
    # SA-1490 Phase 1: accept the pinned worker contract version from the WCP
    # dispatch. Tolerated (and plumbed onto ExecutionTask) but NOT yet honored —
    # routing stays version-blind until Phase 3. Having the field here is also
    # the safety fix: the Event-Mesh intake is ``ExecuteTaskInput(**data)``
    # (main.py), so once WCP sends a ``spec_version`` key this must exist or the
    # call raises ``TypeError: unexpected keyword argument``.
    spec_version: str = ""
    # SA-2424: optional per-node instance pin from WCP dispatch (debug routing).
    # proto3-additive like spec_version above — "" until WCP resolves and sends
    # one, and the Event-Mesh intake is ExecuteTaskInput(**data), so the field
    # must exist here or an incoming target_instance_id key raises TypeError.
    target_instance_id: str = ""
    # SA-2437: host label for the pinned instance — routing FALLBACK when
    # target_instance_id is stale (synthesized ids churn per restart on a machine).
    target_host: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    input: dict[str, Any] = field(default_factory=dict)
    node_config: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    # SA-2030: tenant of the run, from WCP dispatch. Load-bearing for pull tenant
    # isolation — it becomes the ready-store partition key + the callback's tenant
    # binding. proto3-additive: "" until WCP sends it (single un-partitioned
    # namespace, unchanged behaviour); a real value makes isolation end-to-end.
    tenant: str = ""
    # SA-2032: WCP's run generation. Carried on the pull row + echoed on the
    # lifecycle/result events so WCP can reject a stale-generation result and so
    # purge_by_run can drop a reset run's tasks. proto3-additive ("" until sent).
    run_generation: str = ""


@dataclass
class ExecuteTaskOutput:
    task_id: str = ""
    run_id: str = ""
    node_id: str = ""
    status: str = ""
    output_reference: str = ""
    output_metadata: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: int = 0
    idempotency_key: str = ""
    error: dict[str, Any] | None = None


class ExecuteTaskUseCase:
    def __init__(
        self,
        logger: ILogger,
        task_queue: ITaskQueue,
        result_publisher: IResultPublisher,
        decision_switch: IDecisionSwitchClient,
        worker_dispatcher: Any = None,
        result_reporter: IResultReporter | None = None,
        pending_task_store: IPendingTaskStore | None = None,
        concurrency_control: ConcurrencyControl | None = None,
        monitor: Any = None,
        trace_store: Any = None,
        app_config: IAppConfig | None = None,
        # SA-2026 (pull delivery). ``ready_task_store`` is the feature gate: when
        # the pull feature is not wired (default deployments) it is None and the
        # pull branch below is skipped entirely, so the push path is byte-for-byte
        # unchanged. ``registry_store`` (already in the DI pool, injected by name)
        # is the source of truth for a worker's delivery_mode.
        ready_task_store: IReadyTaskStore | None = None,
        registry_store: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.task_queue = task_queue
        self.result_publisher = result_publisher
        self.decision_switch = decision_switch
        self.worker_dispatcher = worker_dispatcher
        self.result_reporter = result_reporter
        self.pending_task_store = pending_task_store
        self.ready_task_store = ready_task_store
        self.registry_store = registry_store
        self._retry_policy = RetryPolicy()
        self._concurrency = concurrency_control or ConcurrencyControl()
        self._monitor = monitor
        self._trace_store = trace_store
        self._app_config = app_config

    def execute(self, input: ExecuteTaskInput) -> dict:
        task_payload = input.input or input.payload or {}
        self.logger.info("Executing task", task_id=input.task_id, run_id=input.run_id)


        if ":" in input.worker_type:
            input.worker_type = input.worker_type.split(":")[1]

        # --- Performance tracing ---
        trace = None
        if self._trace_store:
            trace = self._trace_store.start_trace(
                task_id=input.task_id,
                run_id=input.run_id,
                node_id=input.node_id,
                worker_type=input.worker_type,
            )
        if self._monitor and hasattr(self._monitor, "task_received"):
            self._monitor.task_received(input.worker_type)

        t0 = time.monotonic()

        task = ExecutionTask(
            id=input.task_id,
            run_id=input.run_id,
            node_id=input.node_id,
            worker_type=input.worker_type,
            spec_version=input.spec_version,
            target_instance_id=input.target_instance_id,
            target_host=input.target_host,
            payload=task_payload,
            node_config=input.node_config or {},
            idempotency_key=input.idempotency_key,
        )

        # --- PULL PATH (delivery_mode=pull): store the task for a worker to lease ---
        # Gated on ``ready_task_store``: when the pull feature is not wired (the
        # default) this whole block is skipped and everything below is the exact,
        # unchanged push path. A pull worker registers with no reachable endpoint,
        # so ``get_executor`` would return None and the task would fall through the
        # push branches to a bogus empty-result "COMPLETED" — hence pull must be
        # intercepted here, before the executor lookup. Pull does NOT take a
        # dispatch-concurrency slot: its backpressure is the worker's lease-count
        # (inverted) plus admission control (B6), not this gate.
        if self.ready_task_store is not None and (
            self._resolve_delivery_mode(task.worker_type) == DeliveryMode.PULL
        ):
            enqueued_at = datetime.now(timezone.utc).isoformat()
            entry = TaskRow(
                task_id=task.id,
                run_id=task.run_id,
                node_id=task.node_id,
                worker_type=task.worker_type,
                spec_version=task.spec_version,
                payload=task.payload,
                node_config=task.node_config,
                idempotency_key=task.idempotency_key,
                tenant=input.tenant,
                run_generation=input.run_generation,
                enqueued_at=enqueued_at,
                visible_at=enqueued_at,
                delivery_mode=DeliveryMode.PULL.value,
                # SA-2424: carry the instance pin onto the leasable row so claim()
                # can route it to exactly the chosen instance (was dropped here).
                target_instance_id=input.target_instance_id,
                target_host=input.target_host,
            )
            outcome = self.ready_task_store.enqueue(entry)

            if outcome == ENQUEUE_ADMISSION_FULL:
                # Admission full (a byte quota would overflow). This is BACKPRESSURE,
                # not failure: return a REQUEUE the Event-Mesh consumer raises on, so
                # the broker redelivers after the lock TTL once the queue drains —
                # never a bare 429 to WCP, never a silent drop (same rail as
                # CONCURRENCY_LIMIT / WORKER_SATURATED).
                if self._monitor and hasattr(self._monitor, "increment"):
                    try:
                        self._monitor.increment("pull_admission_full")
                    except Exception:
                        pass
                if trace:
                    self._trace_store.finish_trace(trace, "REQUEUE", "ADMISSION_FULL")
                self.logger.warning(
                    "Pull admission full — REQUEUE via redelivery",
                    task_id=task.id, worker_type=task.worker_type,
                )
                return {
                    "task_id": task.id,
                    "run_id": task.run_id,
                    "node_id": task.node_id,
                    "status": "REQUEUE",
                    "error": {
                        "code": "ADMISSION_FULL",
                        "message": "pull queue byte-quota full — redeliver after lock TTL",
                        "retryable": True,
                    },
                }

            newly = outcome == ENQUEUE_OK
            if newly and self.result_publisher:
                # Lifecycle → WCP marks the node QUEUED (B7). This is what stops a
                # scale-to-zero queue-wait from looking like a silent dispatch hang.
                try:
                    self.result_publisher.publish_result(pull_lifecycle.enqueued_event(entry))
                except Exception as exc:
                    self.logger.warning("Failed to publish task.enqueued", task_id=task.id, error=str(exc))
            if self._monitor and hasattr(self._monitor, "increment"):
                try:
                    self._monitor.increment(
                        "task_enqueued_pull" if newly else "task_enqueue_duplicate_pull"
                    )
                except Exception:
                    pass
            if trace:
                self._trace_store.finish_trace(trace, "ENQUEUED" if newly else "DUPLICATE")
            self.logger.info(
                "Task enqueued for pull delivery" if newly
                else "Duplicate pull task — already enqueued; skipping",
                task_id=task.id,
                worker_type=task.worker_type,
            )
            return {
                "task_id": task.id,
                "run_id": task.run_id,
                "node_id": task.node_id,
                "status": "ENQUEUED" if newly else "DUPLICATE",
                "message": (
                    "Task stored for worker to lease (pull delivery)." if newly
                    else "Task already enqueued; redelivery ignored."
                ),
            }

        if not self._concurrency.acquire():
            if self._monitor and hasattr(self._monitor, "concurrency_rejected"):
                self._monitor.concurrency_rejected()
            if trace:
                self._trace_store.finish_trace(trace, "FAILED", "CONCURRENCY_LIMIT")
            return self._build_error_result(
                task, "CONCURRENCY_LIMIT", "Max concurrent tasks reached"
            )

        task.started_at = datetime.now(timezone.utc).isoformat()

        # --- SA-2424: resolve the executor once, honoring an optional instance pin ---
        # A pinned task must run on THAT instance or not at all; a pin that can't be
        # honored fails the task fast (non-retryable) rather than silently routing to
        # another replica. Done here, after the concurrency slot is held, so the
        # release path is the single `finally`/explicit-release already in place.
        try:
            executor = self._resolve_executor(task)
        except InstancePinUnavailableError as exc:
            self._concurrency.release()
            self.logger.error(
                "Instance pin unavailable — failing task",
                task_id=task.id,
                worker_type=task.worker_type,
                target_instance_id=task.target_instance_id,
                reason=exc.reason,
            )
            if trace:
                self._trace_store.finish_trace(trace, "FAILED", "INSTANCE_PIN_UNAVAILABLE")
            if self.result_reporter and task.run_id:
                try:
                    self.result_reporter.report_failed(
                        run_id=task.run_id,
                        task_id=task.id,
                        error={
                            "code": "INSTANCE_PIN_UNAVAILABLE",
                            "message": str(exc),
                            "retryable": False,
                        },
                    )
                except Exception as report_err:
                    self.logger.error(
                        "Failed to report instance-pin failure to WCP",
                        task_id=task.id, error=str(report_err),
                    )
            return self._build_error_result(task, "INSTANCE_PIN_UNAVAILABLE", str(exc))

        # --- ASYNC PATH: fire-and-forget, result via callback ---
        if self.worker_dispatcher and self.pending_task_store:
            if executor:
                callback_base = getattr(self._app_config, "CALLBACK_BASE_URL", "")
                service_port = getattr(self._app_config, "SERVICE_PORT", 8004)
                # SA-1531: per-type override (long-running types e.g. agent)
                timeout_seconds = self._task_timeout_for(task)
                callback_url = (
                    f"{callback_base or f'http://localhost:{service_port}'}"
                    f"/api/v1/tasks/callback"
                )
                timeout_at = (
                    datetime.now(timezone.utc)
                    + timedelta(seconds=timeout_seconds)
                ).isoformat()
                entry = PendingTaskEntry(
                    task_id=task.id,
                    run_id=task.run_id,
                    node_id=task.node_id,
                    worker_type=task.worker_type,
                    dispatched_at=datetime.now(timezone.utc).isoformat(),
                    timeout_at=timeout_at,
                    payload=task.payload,
                    node_config=task.node_config,
                    idempotency_key=task.idempotency_key,
                    attempt_count=task.attempt_count,
                )
                # Dedup (Event Mesh at-least-once): atomically claim the task by
                # id. If it's already in flight — a redelivery (e.g. a slow
                # callback, or a sibling consumer thread/instance picked it up)
                # — do NOT dispatch again. Release the slot we acquired and ack
                # (the original delivery owns the lifecycle). Releasing here
                # keeps the concurrency counter exact under concurrent consumers.
                if not self.pending_task_store.store_if_absent(entry):
                    self._concurrency.release()
                    self.logger.info(
                        "Duplicate task delivery — already in flight; skipping dispatch",
                        task_id=task.id,
                    )
                    if trace:
                        self._trace_store.finish_trace(trace, "DUPLICATE")
                    return {
                        "task_id": task.id,
                        "run_id": task.run_id,
                        "node_id": task.node_id,
                        "status": "DUPLICATE",
                        "message": "Task already in flight; redelivery ignored.",
                    }

                dispatch_span = trace.add_span("dispatch_async", worker_type=task.worker_type) if trace else None
                try:
                    executor.dispatch({
                        "task_id": task.id,
                        "action": "execute",
                        "inputs": task.payload,
                        "parameters": task.node_config,
                        "correlation_id": task.run_id,
                        "callback_url": callback_url,
                        "worker_type": task.worker_type,
                    })
                except Exception as exc:
                    sat = _saturation_status(exc)
                    self.pending_task_store.remove(task.id)
                    self._concurrency.release()
                    if sat is not None:
                        # SA-1531: worker saturation is BACKPRESSURE, not
                        # failure. No task.failed to WCP, no retry-budget
                        # consumption — the consume callback raises on this
                        # code so the broker skips the ack and Event Mesh
                        # redelivers after the lock TTL (same rail as
                        # CONCURRENCY_LIMIT).
                        if dispatch_span and trace:
                            trace.end_span(dispatch_span, error="WORKER_SATURATED")
                            self._trace_store.finish_trace(trace, "REQUEUE", "WORKER_SATURATED")
                        self.logger.warning(
                            "Worker saturated — REQUEUE via redelivery",
                            task_id=task.id,
                            worker_type=task.worker_type,
                            http_status=sat,
                        )
                        if self._monitor and hasattr(self._monitor, "increment"):
                            try:
                                self._monitor.increment("worker_saturated_requeue")
                            except Exception:
                                pass
                        return {
                            "task_id": task.id,
                            "run_id": task.run_id,
                            "node_id": task.node_id,
                            "status": "REQUEUE",
                            "error": {
                                "code": "WORKER_SATURATED",
                                "message": f"worker acceptance returned {sat} — redeliver after lock TTL",
                                "retryable": True,
                            },
                        }
                    if dispatch_span and trace:
                        trace.end_span(dispatch_span, error=str(exc))
                        self._trace_store.finish_trace(trace, "FAILED", str(exc))
                    return self._handle_execution_error(task, exc)

                if dispatch_span and trace:
                    trace.end_span(dispatch_span)

                dispatch_ms = (time.monotonic() - t0) * 1000
                self.logger.debug(
                    "Task dispatched (async)",
                    task_id=task.id,
                    worker_type=task.worker_type,
                    dispatch_ms=round(dispatch_ms, 2),
                )
                if self._monitor and hasattr(self._monitor, "task_dispatched_async"):
                    self._monitor.task_dispatched_async(task.worker_type)
                    self._monitor.worker_dispatch_duration(task.worker_type, dispatch_ms / 1000)
                # Trace stays active until callback arrives
                return {
                    "task_id": task.id,
                    "run_id": task.run_id,
                    "node_id": task.node_id,
                    "status": "DISPATCHED",
                    "message": "Task dispatched. Result via callback.",
                }

        # --- SYNC PATH: blocking call, release concurrency in finally ---
        try:
            worker_result = None
            if self.worker_dispatcher:
                # executor was resolved once above (honors the SA-2424 instance pin)
                if executor:
                    self.logger.debug(
                        "Dispatching to worker (sync)",
                        task_id=input.task_id,
                        worker_type=task.worker_type,
                    )
                    dispatch_span = trace.add_span("dispatch_sync", worker_type=task.worker_type) if trace else None
                    worker_result = executor.execute({
                        "task_id": task.id,
                        "action": "execute",
                        "inputs": task.payload,
                        "parameters": task.node_config,
                        "correlation_id": task.run_id,
                        "worker_type": task.worker_type,
                    })
                    if dispatch_span and trace:
                        trace.end_span(dispatch_span, status_code=worker_result.get("status", ""))

            task.completed_at = datetime.now(timezone.utc).isoformat()

            output_reference = ""
            output_metadata: dict[str, Any] = {}
            execution_time_ms = 0
            worker_status = ""
            worker_error = ""

            if worker_result:
                worker_outputs = worker_result.get("outputs", worker_result.get("output", {}))
                worker_status = (worker_result.get("status") or "").upper()
                worker_error = worker_result.get("error") or ""
                if isinstance(worker_outputs, dict) and "uri" in worker_outputs:
                    output_reference = worker_outputs.get("uri", "")
                    output_metadata = worker_outputs.get("output_metadata", {})
                else:
                    output_reference = worker_result.get("output_reference", "")
                    output_metadata = worker_outputs if isinstance(worker_outputs, dict) else {}
                execution_time_ms = worker_result.get("duration_ms", worker_result.get("metrics", {}).get("duration_ms", 0))

            # Check if the worker reported an error status (e.g. HTTP 4xx/5xx)
            is_worker_error = worker_status in ("ERROR", "FAILED")

            # --- Report to WCP ---
            report_span = trace.add_span("report_to_wcp") if trace else None
            if self.result_reporter and task.run_id:
                try:
                    if is_worker_error:
                        self.result_reporter.report_failed(
                            run_id=task.run_id,
                            task_id=task.id,
                            error={
                                "code": "WORKER_ERROR",
                                "message": worker_error or "Worker reported error",
                                "retryable": False,
                            },
                            outputs=output_metadata,
                        )
                    else:
                        self.result_reporter.report_completed(
                            run_id=task.run_id,
                            task_id=task.id,
                            output_reference=output_reference,
                            output_metadata=output_metadata,
                        )
                except Exception as report_err:
                    self.logger.error(
                        "Failed to report task result to WCP",
                        task_id=input.task_id,
                        error=str(report_err),
                    )
            if report_span and trace:
                trace.end_span(report_span)

            final_status = "FAILED" if is_worker_error else "COMPLETED"
            msg_type = "task.failed" if is_worker_error else "task.completed"

            result = ExecuteTaskOutput(
                task_id=task.id,
                run_id=task.run_id,
                node_id=task.node_id,
                status=final_status,
                output_reference=output_reference,
                output_metadata=output_metadata,
                execution_time_ms=execution_time_ms,
                idempotency_key=task.idempotency_key,
                error={"code": "WORKER_ERROR", "message": worker_error} if is_worker_error else None,
            )

            if self.result_publisher:
                self.result_publisher.publish_result({
                    "message_type": msg_type,
                    "correlation_id": task.run_id,
                    "payload": result.__dict__,
                })

            total_s = time.monotonic() - t0
            self.logger.debug(
                "Task %s", final_status.lower(),
                task_id=input.task_id,
                total_ms=round(total_s * 1000, 2),
                worker_ms=execution_time_ms,
            )
            if is_worker_error:
                if self._monitor and hasattr(self._monitor, "task_failed"):
                    self._monitor.task_failed(task.worker_type)
            else:
                if self._monitor and hasattr(self._monitor, "task_completed"):
                    self._monitor.task_completed(task.worker_type, total_s)
            if trace:
                self._trace_store.finish_trace(trace, final_status)

            return result.__dict__
        except Exception as exc:
            if trace:
                self._trace_store.finish_trace(trace, "FAILED", str(exc))
            if self._monitor and hasattr(self._monitor, "task_failed"):
                self._monitor.task_failed(task.worker_type)
            return self._handle_execution_error(task, exc)
        finally:
            self._concurrency.release()

    def _resolve_executor(self, task: ExecutionTask) -> Any:
        """Resolve the worker executor for this task (SA-2424).

        With no pin this is the unchanged freshest-healthy selection. With a
        ``target_instance_id`` it dispatches to THAT instance only and raises
        ``InstancePinUnavailableError`` (propagated to the caller) if the instance
        is not eligible — a debug pin must never silently fall back to another
        replica. Returns None when no dispatcher is wired (unchanged behaviour).
        """
        if not self.worker_dispatcher:
            return None
        target_instance_id = (getattr(task, "target_instance_id", "") or "").strip()
        target_host = (getattr(task, "target_host", "") or "").strip()
        if target_instance_id or target_host:
            return self.worker_dispatcher.get_executor_for_instance(
                target_instance_id, task.worker_type, target_host
            )
        return self.worker_dispatcher.get_executor(task.worker_type)

    def _resolve_delivery_mode(self, worker_type: str) -> DeliveryMode:
        """Resolve a task's delivery mode from the target worker's registration.

        Mode is a property of the registration, not the dispatch (WCP is
        transparent to it), so the executor looks it up by worker_type. Any
        uncertainty — no registry wired, worker not (yet) registered, lookup
        error — falls back to PUSH: the pull branch must never hijack a task it
        isn't certain is pull, and an unregistered worker takes the existing push
        path (which then no-ops to a placeholder result, unchanged behaviour).
        """
        if self.registry_store is None:
            return DeliveryMode.PUSH
        try:
            worker = self.registry_store.get_by_worker_type(worker_type)
        except Exception:
            return DeliveryMode.PUSH
        if worker is None:
            return DeliveryMode.PUSH
        mode = getattr(worker, "delivery_mode", DeliveryMode.PUSH)
        return mode if isinstance(mode, DeliveryMode) else DeliveryMode.PUSH

    # Input keys through which a node declares the deadline for its OWN outbound
    # call. ``timeout_seconds`` is the fleet convention (http-request-worker,
    # database-connection-worker). A worker that declares nothing is unaffected.
    _NODE_TIMEOUT_INPUT_KEYS = ("timeout_seconds",)

    @staticmethod
    def _positive_int(value: Any) -> int | None:
        """Coerce a node input to a positive int, or None. The value arrives from
        the control plane as whatever the form produced — "300", 300, 300.0, an
        unresolved "${...}" template, or None — so this never raises."""
        if value is None or isinstance(value, bool):
            return None
        try:
            parsed = int(float(value))
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _declared_timeout_seconds(self, task: ExecutionTask) -> int | None:
        """The deadline the TASK itself declares, if any.

        Deliberately does NOT consult ``ExecutionTask.timeout_ms``: that field is
        declared on the entity but no dispatcher populates it, so every task
        carries the dataclass default. Reading it would invent a contract the
        control plane does not honour.
        """
        for source in (task.payload, task.node_config):
            if not isinstance(source, dict):
                continue
            for key in self._NODE_TIMEOUT_INPUT_KEYS:
                declared = self._positive_int(source.get(key))
                if declared is not None:
                    return declared
        return None

    def _task_timeout_for(self, task: ExecutionTask) -> int:
        """How long to wait for this task's callback before declaring WORKER_TIMEOUT.

        Config sets the FLOOR; the task's own declared deadline can only push it UP:

          1. ``TASK_TIMEOUT_OVERRIDES[worker_type]``, else ``TASK_TIMEOUT_SECONDS``.
          2. If the node declares its own deadline, raise the floor to that
             deadline + ``TASK_CALLBACK_GRACE_SECONDS``.
          3. Clamp to ``TASK_TIMEOUT_MAX_SECONDS`` — a declared deadline is
             caller-supplied data and must not pin a concurrency slot for a week.

        Step 2 is the invariant this method exists to hold: **the executor must
        never give up before the worker's own deadline.** The http worker accepts
        a timeout well above the 120s default here, so without step 2 every node
        configured above the default was swept as
        ``Worker http_request did not callback in time`` while the worker was
        still waiting on a healthy upstream and about to answer — a false failure
        on a task that would have succeeded.

        The override key is the ``worker_type`` (``http_request``), never the CF
        app name (``http-request-worker``); see TASK_TIMEOUT_OVERRIDES in
        ``app_config.py`` for why that distinction has already bitten once.
        """
        default = int(getattr(self._app_config, "TASK_TIMEOUT_SECONDS", 120))
        grace = int(getattr(self._app_config, "TASK_CALLBACK_GRACE_SECONDS", 30))
        ceiling = int(getattr(self._app_config, "TASK_TIMEOUT_MAX_SECONDS", 3600))

        floor = default
        raw = getattr(self._app_config, "TASK_TIMEOUT_OVERRIDES", "") or ""
        if raw:
            try:
                import json as _json
                overrides = _json.loads(raw) if isinstance(raw, str) else dict(raw)
                floor = int(overrides.get(task.worker_type, default))
            except Exception:
                self.logger.warning(
                    "TASK_TIMEOUT_OVERRIDES unparseable — using default",
                    default=default,
                )
                floor = default

        resolved = floor
        declared = self._declared_timeout_seconds(task)
        if declared is not None and declared + grace > resolved:
            resolved = declared + grace
            self.logger.info(
                "Pending timeout raised to cover the node's own deadline",
                task_id=task.id,
                worker_type=task.worker_type,
                declared_timeout_seconds=declared,
                pending_timeout_seconds=min(resolved, ceiling),
            )

        return max(1, min(resolved, ceiling))

    def _handle_execution_error(
        self, task: ExecutionTask, error: Exception, outputs: dict[str, Any] | None = None,
    ) -> dict:
        error_code = "WORKER_ERROR"
        error_msg = str(error)

        retry_decision = self._retry_policy.evaluate(task.attempt_count, error_code)

        if retry_decision.should_retry:
            self.logger.warning(
                "Task failed, will retry",
                task_id=task.id,
                attempt=task.attempt_count,
                next_delay=retry_decision.delay_seconds,
            )
        else:
            self.logger.error("Task failed permanently", task_id=task.id, error=error_msg)

        result = {
            "message_type": "task.failed",
            "correlation_id": task.run_id,
            "payload": {
                "task_id": task.id,
                "run_id": task.run_id,
                "node_id": task.node_id,
                "status": "FAILED",
                "error": {
                    "code": error_code,
                    "message": error_msg,
                    "retryable": retry_decision.should_retry,
                },
                "attempt": task.attempt_count,
                "idempotency_key": task.idempotency_key,
                "outputs": outputs or {},
            },
        }

        if self.result_reporter and task.run_id:
            try:
                self.result_reporter.report_failed(
                    run_id=task.run_id,
                    task_id=task.id,
                    error={
                        "code": error_code,
                        "message": error_msg,
                        "retryable": retry_decision.should_retry,
                    },
                    outputs=outputs or {},
                )
            except Exception as report_err:
                self.logger.error(
                    "Failed to report failure to WCP",
                    task_id=task.id,
                    error=str(report_err),
                )

        if self.result_publisher:
            self.result_publisher.publish_result(result)

        return result["payload"]

    def _build_error_result(
        self, task: ExecutionTask, error_code: str, message: str
    ) -> dict:
        return {
            "task_id": task.id,
            "run_id": task.run_id,
            "node_id": task.node_id,
            "status": "FAILED",
            "error": {"code": error_code, "message": message, "retryable": False},
        }
