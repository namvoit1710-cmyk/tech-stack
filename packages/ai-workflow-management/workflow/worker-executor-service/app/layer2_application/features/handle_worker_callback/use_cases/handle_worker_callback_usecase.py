"""Handle worker callback: receive async result, release concurrency, report to WCP."""

from __future__ import annotations

import json

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.errors import MessageTooLargeError, ResultUndeliverableError
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.pending_task_store_interface import (
    IPendingTaskStore,
    PendingTaskEntry,
)
from app.layer2_application.interfaces.ready_task_store_interface import (
    IReadyTaskStore,
    COMPLETE_OK,
)
from app.layer2_application.interfaces.result_reporter_interface import IResultReporter
from app.layer2_application.interfaces.result_publisher_interface import IResultPublisher
from app.layer2_application.services.concurrency_control import ConcurrencyControl
from app.layer2_application.services import pull_lifecycle


def _is_superseded_rejection(exc: BaseException) -> bool:
    """Return True when ``exc`` represents a control-plane 409 rejection
    for a superseded task generation (``TASK_SUPERSEDED``). These are
    expected outcomes when a worker finishes a task after the run has
    been reset and re-executed; the worker cannot and should not retry.
    """
    # Check HTTP status code without importing httpx at module load time
    # (keeps this util usable under both HTTP and gRPC reporters).
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code != 409:
        return False
    try:
        body = response.json()
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    # FastAPI error envelope: {"status": "error", "error": {"code": "TASK_SUPERSEDED", ...}}
    err = body.get("error")
    if isinstance(err, dict) and err.get("code") == "TASK_SUPERSEDED":
        return True
    return False


@dataclass
class HandleWorkerCallbackInput:
    task_id: str = ""
    status: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    duration_ms: float = 0.0
    output_reference: str = ""
    # SA-2030 (pull callback). Triple-binding credentials — verified against the
    # STORED row, never trusted from the body. lease_token is the cryptographic
    # gate; tenant/worker_type are the isolation claims (XSUAA-derived at the
    # gateway in prod; header-carried here). Empty on a push callback.
    lease_token: str = ""
    tenant: str = ""
    worker_type: str = ""


@dataclass
class HandleWorkerCallbackOutput:
    task_id: str = ""
    accepted: bool = False
    message: str = ""


class HandleWorkerCallbackUseCase:
    def __init__(
        self,
        logger: ILogger,
        pending_task_store: IPendingTaskStore,
        result_reporter: IResultReporter | None = None,
        result_publisher: IResultPublisher | None = None,
        concurrency_control: ConcurrencyControl | None = None,
        monitor: Any = None,
        trace_store: Any = None,
        ready_task_store: IReadyTaskStore | None = None,
        file_uploader: Any = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.pending_task_store = pending_task_store
        self.result_reporter = result_reporter
        self.result_publisher = result_publisher
        self._concurrency = concurrency_control
        self._monitor = monitor
        self._trace_store = trace_store
        self.ready_task_store = ready_task_store
        # Optional. ``None`` keeps the pre-Phase-4 behaviour exactly: an
        # oversized result degrades straight to ``task.failed``.
        self._file_uploader = file_uploader

    def execute(self, input: HandleWorkerCallbackInput) -> dict:
        # PULL callback (SA-2030): if this task_id is a pull task (it lives in the
        # ready store, never the pending store), complete it via the triple-binding
        # CAS. This branch is gated on ready_task_store, so a push-only deployment
        # is byte-for-byte unchanged; and because push tasks are never in the ready
        # store, a pull credential can never complete a push task (and vice versa).
        if self.ready_task_store is not None:
            row = self.ready_task_store.get(input.task_id)
            if row is not None:
                return self._handle_pull_callback(row, input)

        entry = self.pending_task_store.remove(input.task_id)
        if entry is None:
            self.logger.warning(
                "Callback for unknown task",
                task_id=input.task_id,
            )
            return HandleWorkerCallbackOutput(
                task_id=input.task_id,
                accepted=False,
                message="Unknown task",
            ).__dict__

        self.logger.debug(
            "Received worker callback",
            task_id=input.task_id,
            status=input.status,
            duration_ms=input.duration_ms,
        )

        if self._monitor and hasattr(self._monitor, "callback_received"):
            self._monitor.callback_received(input.status)

        # Complete the trace that was started during dispatch
        trace = None
        if self._trace_store:
            trace = self._trace_store.get_by_task_id(input.task_id)
            # get_by_task_id returns a dict; we need the active trace object
            active_trace = self._trace_store.get_active_trace(input.task_id)
            if active_trace:
                active_trace.add_span("worker_execution", duration_ms=input.duration_ms)
                report_span = active_trace.add_span("report_to_wcp")

        # SA-1951 — a restored entry (its result could not be delivered, see
        # ``_publish_or_fail``) has already had its slot released once. Releasing
        # again on the worker's retry would inflate the in-flight budget.
        if self._concurrency and not entry.concurrency_released:
            self._concurrency.release()

        if input.status == "success":
            # Use explicit output_reference if worker provided one;
            # otherwise check for a "uri" key in outputs (file-reference format).
            output_ref = input.output_reference or input.outputs.get("uri", "")
            # The full worker outputs (e.g. status_code, response_body, is_success)
            # are forwarded as output_metadata so the control plane can store them
            # in the run context for downstream node expressions.
            output_metadata = input.outputs

            if self.result_reporter and entry.run_id:
                try:
                    self.result_reporter.report_completed(
                        run_id=entry.run_id,
                        task_id=entry.task_id,
                        output_reference=output_ref,
                        output_metadata=output_metadata,
                    )
                except Exception as exc:
                    # G-guard: 409 TASK_SUPERSEDED is expected on re-execute
                    # — WCP rejected because the run generation moved on.
                    # Terminal: worker result already dropped, nothing to retry.
                    if _is_superseded_rejection(exc):
                        self.logger.info(
                            "WCP rejected completion: task superseded",
                            task_id=input.task_id,
                            run_id=entry.run_id,
                        )
                    else:
                        self.logger.error(
                            "Failed to report completion to WCP",
                            task_id=input.task_id,
                            error=str(exc),
                        )

            if self.result_publisher:
                self._publish_or_fail(entry, {
                    "message_type": "task.completed",
                    "correlation_id": entry.run_id,
                    "payload": {
                        "task_id": entry.task_id,
                        "run_id": entry.run_id,
                        "node_id": entry.node_id,
                        "status": "COMPLETED",
                        "output_reference": output_ref,
                        "output_metadata": output_metadata,
                        "execution_time_ms": input.duration_ms,
                    },
                })
        else:
            error_info = {
                "code": "WORKER_ERROR",
                "message": input.error or "Unknown error",
                "retryable": False,
            }
            if self.result_reporter and entry.run_id:
                try:
                    self.result_reporter.report_failed(
                        run_id=entry.run_id,
                        task_id=entry.task_id,
                        error=error_info,
                        outputs=input.outputs,
                    )
                except Exception as exc:
                    if _is_superseded_rejection(exc):
                        self.logger.info(
                            "WCP rejected failure: task superseded",
                            task_id=input.task_id,
                            run_id=entry.run_id,
                        )
                    else:
                        self.logger.error(
                            "Failed to report failure to WCP",
                            task_id=input.task_id,
                            error=str(exc),
                        )

            if self.result_publisher:
                self._publish_or_fail(entry, {
                    "message_type": "task.failed",
                    "correlation_id": entry.run_id,
                    "payload": {
                        "task_id": entry.task_id,
                        "run_id": entry.run_id,
                        "node_id": entry.node_id,
                        "status": "FAILED",
                        "error": error_info,
                        "outputs": input.outputs,
                    },
                })

        # Finish the active trace
        if self._trace_store:
            active_trace = self._trace_store.get_active_trace(input.task_id)
            if active_trace:
                # End the report_to_wcp span if it was started
                if active_trace.spans and active_trace.spans[-1].name == "report_to_wcp":
                    active_trace.end_span(active_trace.spans[-1])
                final_status = "COMPLETED" if input.status == "success" else "FAILED"
                self._trace_store.finish_trace(
                    active_trace,
                    final_status,
                    error=input.error or "",
                )

        if self._monitor and hasattr(self._monitor, "task_completed") and input.status == "success":
            self._monitor.task_completed(entry.worker_type, input.duration_ms / 1000)
        elif self._monitor and hasattr(self._monitor, "task_failed") and input.status != "success":
            self._monitor.task_failed(entry.worker_type)

        return HandleWorkerCallbackOutput(
            task_id=input.task_id,
            accepted=True,
            message="OK",
        ).__dict__

    # ------------------------------------------------------------------
    # SA-2030 — pull callback: triple-binding CAS complete, then report
    # ------------------------------------------------------------------
    def _handle_pull_callback(self, row: Any, input: HandleWorkerCallbackInput) -> dict:
        # Complete FIRST — the CAS both authorizes (triple-binding, all resolved
        # from the STORED row) and marks the task done atomically. Doing it before
        # reporting means a late/superseded/forged worker can never push a stale
        # result to WCP. tenant/worker_type are enforced only when the caller
        # presented them (a bare push-shaped callback that lands here without them
        # still needs the exact lease_token).
        # Enforce the FULL triple-binding: pass the caller's tenant/worker_type
        # verbatim (even empty), so an empty value is COMPARED to the row (and
        # rejected when the row is non-empty) rather than skipping the check. A
        # token-holder cannot bypass the tenant/type binding by omitting it.
        outcome = self.ready_task_store.complete(
            input.task_id,
            input.lease_token,
            tenant=input.tenant,
            worker_type=input.worker_type,
        )
        if outcome != COMPLETE_OK:
            # Uniform opaque rejection: SUPERSEDED (bad lease/tenant/type/state) and
            # UNKNOWN (already completed / purged) both look identical to the caller
            # — no existence or timing oracle. The controller maps this to 409.
            self.logger.info(
                "Pull callback rejected", task_id=input.task_id, outcome=outcome,
            )
            if self._monitor and hasattr(self._monitor, "increment"):
                try:
                    self._monitor.increment("pull_callback_rejected")
                except Exception:
                    pass
            return HandleWorkerCallbackOutput(
                task_id=input.task_id, accepted=False, message="TASK_SUPERSEDED",
            ).__dict__

        # Authorized + completed. Report to WCP + publish using the STORED row's
        # identity (run_id/node_id/worker_type) — never the request's. Pull holds
        # no dispatch-concurrency slot, so nothing to release.
        succeeded = input.status == "success"
        output_ref = input.output_reference or input.outputs.get("uri", "")
        safe_outputs = input.outputs
        error_info = {"code": "WORKER_ERROR", "message": input.error or "Unknown error", "retryable": False}

        # UNTRUSTED_OUTPUT_POINTER (B7): a worker holding a valid lease may still name
        # a file_id it doesn't own — in output_reference OR nested anywhere in outputs.
        # Reject a cross-tenant pointer: downgrade to a failure AND scrub the outputs,
        # so the poisoned reference never reaches WCP (as a success OR as failure-outputs).
        if succeeded and not (pull_lifecycle.output_pointer_ok(row.tenant, output_ref)
                              and pull_lifecycle.outputs_ok(row.tenant, input.outputs)):
            self.logger.warning(
                "Pull output pointer rejected (cross-tenant)",
                task_id=input.task_id, tenant=row.tenant,
            )
            succeeded = False
            output_ref = ""
            safe_outputs = {}
            error_info = {"code": "UNTRUSTED_OUTPUT_POINTER",
                          "message": "output file_id does not belong to the task's tenant",
                          "retryable": False}

        # Stable fence echoed to WCP on EVERY result event (B7): idempotency_key +
        # run_generation, from the STORED row — so WCP dedups a re-driven task and
        # rejects a stale-generation result. lease_generation is the monotonic
        # ordering token (PR review) so a completion orders correctly vs lifecycle events.
        fence = {"idempotency_key": row.idempotency_key, "run_generation": row.run_generation,
                 "lease_generation": row.lease_generation}

        if succeeded:
            if self.result_reporter and row.run_id:
                try:
                    self.result_reporter.report_completed(
                        run_id=row.run_id, task_id=row.task_id,
                        output_reference=output_ref, output_metadata=input.outputs,
                        run_generation=row.run_generation, idempotency_key=row.idempotency_key,
                    )
                except Exception as exc:
                    if _is_superseded_rejection(exc):
                        self.logger.info("WCP rejected pull completion: superseded", task_id=input.task_id)
                    else:
                        self.logger.error("Failed to report pull completion to WCP", task_id=input.task_id, error=str(exc))
            if self.result_publisher:
                try:
                    self.result_publisher.publish_result({
                        "message_type": "task.completed",
                        "correlation_id": row.run_id,
                        "payload": {
                            "task_id": row.task_id, "run_id": row.run_id, "node_id": row.node_id,
                            "status": "COMPLETED", "output_reference": output_ref,
                            "output_metadata": input.outputs, "execution_time_ms": input.duration_ms,
                            **fence,
                        },
                    })
                except Exception as exc:
                    self.logger.error("Failed to publish pull completion", task_id=input.task_id, error=str(exc))
        else:
            if self.result_reporter and row.run_id:
                try:
                    self.result_reporter.report_failed(
                        run_id=row.run_id, task_id=row.task_id, error=error_info, outputs=safe_outputs,
                        run_generation=row.run_generation, idempotency_key=row.idempotency_key,
                    )
                except Exception as exc:
                    if _is_superseded_rejection(exc):
                        self.logger.info("WCP rejected pull failure: superseded", task_id=input.task_id)
                    else:
                        self.logger.error("Failed to report pull failure to WCP", task_id=input.task_id, error=str(exc))
            if self.result_publisher:
                try:
                    self.result_publisher.publish_result({
                        "message_type": "task.failed",
                        "correlation_id": row.run_id,
                        "payload": {
                            "task_id": row.task_id, "run_id": row.run_id, "node_id": row.node_id,
                            "status": "FAILED", "error": error_info, "outputs": safe_outputs,
                            **fence,
                        },
                    })
                except Exception as exc:
                    self.logger.error("Failed to publish pull failure", task_id=input.task_id, error=str(exc))

        if self._monitor:
            if succeeded and hasattr(self._monitor, "task_completed"):
                self._monitor.task_completed(row.worker_type, input.duration_ms / 1000)
            elif not succeeded and hasattr(self._monitor, "task_failed"):
                self._monitor.task_failed(row.worker_type)

        return HandleWorkerCallbackOutput(
            task_id=input.task_id, accepted=True, message="OK",
        ).__dict__

    def _offload_result(self, entry: PendingTaskEntry, message: dict) -> bool:
        """Deliver an oversized COMPLETED result as a pointer. True when published.

        Never raises: every failure path returns ``False`` and the caller falls
        back to the ``task.failed`` degrade that existed before this. The
        degradation ladder only ever gets longer.

        **JSON, not CSV.** ``data_store_service`` stores and reads a verbatim
        JSON payload (by content-type or a leading brace), so the control plane
        resolves this ref without ``dict_to_csv`` — the 5.5-9.3x blow-up SA-1905
        deliberately moved OFF the control plane. Converting here would re-import
        that OOM risk into a service that runs at ``instances: 1``.

        The ref is handed over whole because ``complete_task`` skips
        ``store_data`` when the output is already a file_ref — so what is built
        here is what the run context ends up holding.

        Only ``task.completed`` is offloaded. When the task itself FAILED, the
        worker's error is the news and the oversized ``outputs`` riding along are
        exactly what to drop; uploading them would preserve the least useful half.
        """
        payload = message.get("payload") or {}
        outputs = payload.get("output_metadata")
        if message.get("message_type") != "task.completed":
            return False
        if not isinstance(outputs, dict) or not outputs:
            return False

        try:
            blob = json.dumps(outputs, default=str).encode("utf-8")
            file_id = self._file_uploader.upload(
                blob, f"{entry.task_id}-output.json", "application/json",
            )
        except Exception as exc:
            self.logger.error(
                "Result offload failed to upload — degrading to task.failed",
                task_id=entry.task_id, run_id=entry.run_id, error=str(exc),
            )
            return False
        if not file_id:
            return False

        ref = {
            "__file_ref": True,
            "artifact_id": "__root__",
            "file_id": file_id,
            "content_type": "application/json",
            "row_count": 0,
            "columns": [],
            "column_types": {},
            "schema_hash": "",
            # The outputs dict IS an object. Declared rather than left blank so
            # no reader has to guess it back (SA-1977).
            "value_kind": "object",
            "item_kind": "",
            "size_bytes": len(blob),
            "artifacts": [],
            "version": 0,
            "etag": "",
        }
        slim = {
            **message,
            "payload": {
                **payload,
                "output_metadata": ref,
                "output_reference": file_id,
            },
        }
        try:
            self.result_publisher.publish_result(slim)
        except Exception as exc:
            self.logger.error(
                "Result offload published a pointer that was STILL rejected — "
                "degrading to task.failed",
                task_id=entry.task_id, run_id=entry.run_id, error=str(exc),
            )
            return False

        self.logger.warning(
            "Result was too large to publish inline; delivered by pointer",
            task_id=entry.task_id, run_id=entry.run_id,
            file_id=file_id, size_bytes=len(blob),
        )
        if self._monitor and hasattr(self._monitor, "increment"):
            try:
                self._monitor.increment("result_offloaded")
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------
    # SA-1951 — the publish is the ONLY route home
    # ------------------------------------------------------------------
    def _publish_or_fail(self, entry: PendingTaskEntry, message: dict) -> None:
        """Publish *message*, and never lose the task silently if that fails.

        In ``event_mesh`` mode (tenant-1) ``result_reporter`` is None — see
        ``bootstrap`` — so this publish is the **single** route back to the
        control plane. It used to be unguarded: an oversized inline output made
        the broker reject the message, the exception escaped, the worker ignored
        the resulting 500, and because the pending entry had already been popped
        the sweeper could not time the task out either. The node stayed RUNNING
        forever, with no error anywhere (SA-1951).

        Degrade instead:
        1. publish the real result;
        2. on failure, publish a **small** ``task.failed`` so the run fails fast
           with a greppable cause (the payload the caller handed us is the very
           thing the broker refused — send a pointer-sized one instead);
        3. if even that fails the broker is unreachable: put the entry back so
           the sweeper still times the task out, and raise so the worker retries.
        """
        try:
            self.result_publisher.publish_result(message)
            return
        except Exception as exc:
            reason = exc
            self.logger.error(
                "Failed to publish worker result — degrading to task.failed",
                task_id=entry.task_id,
                run_id=entry.run_id,
                message_type=message.get("message_type"),
                error=str(exc),
            )

        # Phase 4 — before giving up on the payload, try to deliver it BY
        # POINTER. Only for an oversized result: a broker that is unreachable is
        # not a payload problem, and uploading would not help reach it.
        #
        # This is the one place every delivery path meets. The worker-sdk's
        # streaming and result budget live in its RESTful controller only —
        # neither the pull controller nor the gRPC servicer calls them — but a
        # pull or gRPC callback still arrives here.
        if isinstance(reason, MessageTooLargeError) and self._file_uploader is not None:
            if self._offload_result(entry, message):
                return

        code = (
            "RESULT_TOO_LARGE"
            if isinstance(reason, MessageTooLargeError)
            else "RESULT_UNDELIVERABLE"
        )
        # Count it. Until now the only way to learn this had happened was to
        # read a failed run by hand — which is how run
        # edda4f82-4a09-4587-beb5-8ed615ab8219 was found, days late. The two
        # codes get SEPARATE counters on purpose: an oversized result is a
        # payload-shape problem someone has to fix in a worker, while an
        # undeliverable one is the broker being unreachable. One counter for
        # both would average away the difference.
        #
        # No labels — the label set of a Prometheus metric is frozen on its
        # first call, and a later call with different keys is dropped silently.
        if self._monitor and hasattr(self._monitor, "increment"):
            try:
                self._monitor.increment(code.lower())
            except Exception:
                pass
        # If the task had ALREADY failed, the worker's own error is the news —
        # what could not be delivered is the oversized ``outputs`` riding along
        # with it. Keep the real cause and drop the payload, rather than masking
        # a worker error with a transport one.
        original_error = message.get("payload", {}).get("error")
        error_info = (
            dict(original_error)
            if message.get("message_type") == "task.failed" and isinstance(original_error, dict)
            else {"code": code, "message": str(reason)[:500], "retryable": False}
        )
        error_info["undeliverable"] = code

        try:
            self.result_publisher.publish_result({
                "message_type": "task.failed",
                "correlation_id": entry.run_id,
                "payload": {
                    "task_id": entry.task_id,
                    "run_id": entry.run_id,
                    "node_id": entry.node_id,
                    "status": "FAILED",
                    # No outputs: they are what could not be delivered.
                    "error": error_info,
                },
            })
            self.logger.warning(
                "Worker result was undeliverable — task failed instead of hanging",
                task_id=entry.task_id,
                run_id=entry.run_id,
                code=code,
            )
            return
        except Exception as exc:
            self.logger.error(
                "task.failed fallback ALSO failed — restoring the pending entry "
                "so the sweeper can time this task out",
                task_id=entry.task_id,
                run_id=entry.run_id,
                error=str(exc),
            )

        # The slot was released on the way in; whoever picks this entry up next
        # (a worker retry, or the sweeper) must not release it a second time.
        entry.concurrency_released = True
        self.pending_task_store.store(entry)
        raise ResultUndeliverableError(
            f"could not deliver the result for task {entry.task_id}: {reason}"
        )
