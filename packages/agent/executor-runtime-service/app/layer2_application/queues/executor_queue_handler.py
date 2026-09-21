"""
Executor Queue Handler.

Top-level orchestration for an inbound agent.request.executor message.
Validates, dispatches, and publishes status events.
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.step import Step, StepStatus, DispatchStepRequest
from app.layer1_domain.interfaces.step_status_publisher_interface import (
    IStepStatusPublisher,
    StepStatusPublisherInput,
)
from app.layer2_application.features.dispatch_step.use_cases.dispatch_step_use_case import (
    DispatchStepUseCase,
)
from app.layer2_application.features.validate_step_batch.use_cases.validate_step_batch_use_case import (
    ValidationError,
    validate_step_batch,
)
from app.layer2_application.interfaces.observability import ILogger


class ExecutorQueueHandler:
    def __init__(
        self,
        dispatcher: DispatchStepUseCase,
        status_publisher: IStepStatusPublisher,
        logger: ILogger,
    ) -> None:
        self._dispatcher = dispatcher
        self._status_publisher = status_publisher
        self._logger = logger

    async def handle_step_batch(self, payload: dict[str, Any]) -> None:
        """
        Handle an incoming agent.request.executor envelope.

        SA-892 (N12) SECONDARY durable-stop guard — DELIBERATELY ABSENT here:

            The step-intake is where a "drop the step if the conversation is
            CANCELLED" guard would sit. It reads ``Metadata.status`` via the
            agent shared-state repo — but the executor-runtime-service wires NO
            shared-state repo (see bootstrap.build_app_container /
            ioc_container.build_dependencies: capability_resolver, tool_executor,
            technical_agent_step_executor, retry_policy, status_publisher — no
            IAgentSharedStateRepository), and it has no other route to the
            conversation's durable status. The status is therefore UNREACHABLE
            here without standing up new HANA infrastructure + config in this
            service, which is out of scope for this change.

            The stopped conversation is still protected by two other SA-892
            mechanisms, so this secondary guard is redundant in practice:
              * N4 control fan-out: the orchestrator's stop publishes an
                ``agent.control`` cancel to ``agent.request.executor``. This
                service is an agent-sdk consumer (main.py -> run_consumer_agent),
                so the shared consumer runtime handles that control message inline
                (is_agent_control_message) and cancels the in-flight turn BEFORE
                it ever reaches this handler.
              * PRIMARY business-agent guard: a CANCELLED conversation's
                continuations are dropped at the business-agent's turn entry, so
                the business agent never dispatches a NEW step batch here for a
                stopped conversation.

        Flow:
        1. TODO: Inbox — persist inbound message for idempotency and crash recovery
        2. Validate envelope shape
        3. For each step:
           a. Publish executor.step.status with status=processing
           b. TODO: Execution Store — persist step state RUNNING
           c. Dispatch step (local tool or technical agent)
           d. Publish executor.step.status with status=success or error
           e. TODO: Execution Store — persist step state COMPLETED/FAILED
        4. TODO: Outbox — write status events to outbox instead of direct publish
        """

        # --- Validate ---
        try:
            request = validate_step_batch(payload)
        except ValidationError as exc:
            self._logger.warning(f"Validation failed: {exc.reason}")
            await self._publish_validation_error(payload, exc)
            return

        self._logger.info(
            f"Processing step batch: message_id={request.message_id} batch_id={request.batch_id} steps={len(request.steps)}"
        )

        # --- Process each step sequentially (MVP; TODO: parallel for independent steps) ---
        for step in request.steps:
            self._logger.info(
                "Received step for execution",
                message_id=request.message_id,
                batch_id=request.batch_id,
                correlation_id=request.correlation_id,
                conv_id=request.conv_id,
                step_id=step.id,
                execute_by=step.execute_by,
                input_message=step.message,
                input_data=dict(step.input_data or {}),
                uploaded_file_ids=list(step.uploaded_file_ids or []),
            )

            # Publish processing status
            step.status = StepStatus.PROCESSING
            self._logger.info(
                f"Publishing processing status: step_id={step.id} message_id={request.message_id} step={step.id} status={step.status}"
            )
            await self._status_publisher.publish_status(
                StepStatusPublisherInput(
                    step=step,
                    to_agent=request.from_agent,
                    conv_id=request.conv_id,
                    correlation_id=request.correlation_id,
                )
            )

            # Dispatch
            result = await self._dispatcher.dispatch(
                DispatchStepRequest(
                    step=step,
                    message_id=request.message_id,
                    correlation_id=request.correlation_id,
                    conv_id=request.conv_id,
                    batch_id=request.batch_id,
                    from_agent=request.from_agent,
                )
            )

            # Update step with result
            step.status = result.status
            step.error = result.error
            if result.status == StepStatus.SUCCESS:
                step.output_data = result.output

            # Publish terminal status
            self._logger.info(
                "Publishing step result",
                message_id=request.message_id,
                batch_id=request.batch_id,
                correlation_id=request.correlation_id,
                conv_id=request.conv_id,
                step_id=step.id,
                execute_by=step.execute_by,
                input_message=step.message,
                input_data=dict(step.input_data or {}),
                output_message=self._extract_output_message(step.output_data),
                output_data=dict(step.output_data or {}),
                status=step.status.value if step.status else None,
                error=step.error,
            )
            await self._status_publisher.publish_status(
                StepStatusPublisherInput(
                    step=step,
                    to_agent=request.from_agent,
                    conv_id=request.conv_id,
                    correlation_id=request.correlation_id,
                )
            )

    def _extract_output_message(self, output_data: dict[str, Any]) -> str:
        if not isinstance(output_data, dict):
            return ""

        message = output_data.get("message") or output_data.get("result")
        if isinstance(message, str):
            return message
        return ""

        self._logger.info(
            f"Step batch completed: message_id={request.message_id} batch_id={request.batch_id}"
        )

    async def _publish_validation_error(
        self, payload: dict[str, Any], error: ValidationError
    ) -> None:
        """
        Best-effort error publishing when validation fails.
        If we can extract step info from the raw payload, publish error status.
        """
        conv_id = payload.get("conv_id") or ""
        correlation_id = payload.get("correlation_id") or ""
        message_id = payload.get("message_id") or ""

        raw_payload = payload.get("payload") or {}
        if not isinstance(raw_payload, dict):
            self._logger.error(
                f"Validation error and payload is not an object: {error.reason}"
            )
            return

        from_agent = raw_payload.get("from") or ""
        steps_raw = raw_payload.get("steps") or []

        if not steps_raw or not isinstance(steps_raw, list):
            self._logger.error(
                f"Validation error and cannot identify steps: {error.reason}"
            )
            return

        for step_raw in steps_raw:
            if not isinstance(step_raw, dict):
                continue
            step_id = step_raw.get("id") or "unknown"
            step = Step(
                id=step_id,
                execute_by=step_raw.get("execute_by") or "unknown",
                input_data=step_raw.get("input_data") or {},
                goal=step_raw.get("goal") or "",
                message=step_raw.get("message") or "",
                output_data=step_raw.get("output_data") or {},
                status=StepStatus.ERROR,
                error=f"Validation failed: {error.reason}",
            )
            self._logger.info(
                f"Publishing validation error status: step_id={step.id} message_id={message_id} step={step.id} status={step.status} reason={error.reason}"
            )
            await self._status_publisher.publish_status(
                StepStatusPublisherInput(
                    step=step,
                    to_agent=from_agent,
                    conv_id=conv_id,
                    correlation_id=correlation_id,
                )
            )
