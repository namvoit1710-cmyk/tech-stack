from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid_utils import uuid7

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest

from app.layer1_domain.entities.step import (
    ResolvedCapability,
    Step,
    StepResult,
    StepStatus,
    DispatchStepRequest,
)
from app.layer2_application.interfaces.technical_agent_step_executor_interface import (
    ITechnicalAgentStepExecutor,
)
from app.layer2_application.interfaces.observability import ILogger
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.services.agent_reply_waiter import AgentReplyWaiter


@dataclass(frozen=True)
class NormalizedAgentReply:
    output_data: dict[str, Any]
    error: str | None
    raw_reply: dict[str, Any]


@dataclass(frozen=True)
class TechnicalAgentStepPayloadDto:
    id: str
    execute_by: str
    goal: str
    message: str
    uploaded_file_ids: list[str]
    input_data: dict[str, object]
    output_data: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "execute_by": self.execute_by,
            "goal": self.goal,
            "message": self.message,
            "uploaded_file_ids": list(self.uploaded_file_ids),
            "input_data": dict(self.input_data),
            "output_data": dict(self.output_data),
        }


@dataclass(frozen=True)
class TechnicalAgentMessagePayloadDto:
    from_agent: str
    to_agent: str
    type: str
    step: TechnicalAgentStepPayloadDto

    def to_dict(self) -> dict[str, object]:
        return {
            "from": self.from_agent,
            "to": self.to_agent,
            "type": self.type,
            "step": self.step.to_dict(),
        }


@dataclass(frozen=True)
class TechnicalAgentMessageOverridesDto:
    message_id: str
    conv_id: str
    conversation_id: str
    payload: TechnicalAgentMessagePayloadDto

    def to_dict(self) -> dict[str, object]:
        return {
            "message_id": self.message_id,
            "conv_id": self.conv_id,
            "conversation_id": self.conversation_id,
            "payload": self.payload.to_dict(),
        }


class TechnicalAgentStepExecutor(ITechnicalAgentStepExecutor):
    def __init__(
        self,
        delegator: Any,
        reply_waiter: AgentReplyWaiter,
        reply_topic: str,
        timeout_seconds: float,
        logger: ILogger,
        retry_delegated_on_timeout: bool = False,
    ) -> None:
        self._delegator = delegator
        self._reply_waiter = reply_waiter
        self._reply_topic = reply_topic
        self._timeout_seconds = timeout_seconds
        self._logger = logger
        # A reply timeout does not prove the agent didn't run, so re-delegating could
        # double-execute a non-idempotent agent. Off by default (idempotency guard).
        self._retry_delegated_on_timeout = retry_delegated_on_timeout

    async def execute(
        self,
        request: DispatchStepRequest,
        capability: ResolvedCapability,
    ) -> StepResult:
        step = request.step
        agent_id = self._parse_agent_id(step.execute_by)
        if not agent_id:
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Invalid technical agent execute_by='{step.execute_by}'",
            )

        correlation_id = str(uuid7())
        delegated_message_id = str(uuid7())
        conversation_metadata = self._build_conversation_metadata(request)
        executor_metadata = self._build_executor_metadata(request, correlation_id)
        context_snapshot = self._build_context_snapshot(
            request,
            capability,
            executor_metadata,
        )
        agent_call = AgentCallRequest(
            agent_id=agent_id,
            agent_type=capability.agent_type or agent_id,
            input_payload=dict(step.input_data or {}),
            interrupt_id=request.message_id,
            thread_id=request.conv_id,
            correlation_id=correlation_id,
            session_id=request.batch_id,
            reply_topic=self._reply_topic,
            reply_queue=self._reply_topic,
            queue_metadata=dict(capability.queue_metadata or {}),
            metadata=conversation_metadata,
            context_snapshot=context_snapshot,
            request_message_type=self._resolve_request_message_type(capability),
            response_message_type=self._resolve_response_message_type(capability),
            message_overrides=self._build_message_overrides(
                request=request,
                step=step,
                agent_id=agent_id,
                message_id=delegated_message_id,
            ).to_dict(),
        )

        self._logger.info(
            "Sending step to technical agent",
            step_id=step.id,
            execute_by=step.execute_by,
            agent_id=agent_id,
            agent_type=capability.agent_type,
            delegated_correlation_id=correlation_id,
            reply_topic=self._reply_topic,
            input_message=step.message,
            input_data=dict(step.input_data or {}),
            agent_input_payload=dict(agent_call.input_payload or {}),
            agent_message=agent_call.message_overrides.get("payload", {}).get("step", {}),
            context_snapshot=dict(agent_call.context_snapshot or {}),
        )

        self._reply_waiter.register(correlation_id)
        try:
            await self._delegator.delegate(agent_call)
        except Exception as exc:
            self._reply_waiter.forget(correlation_id)
            self._logger.error(
                "Technical agent delegation failed before wait",
                step_id=step.id,
                agent_id=agent_id,
                error=str(exc),
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Technical agent delegation failed: {exc}",
                retryable=True,  # send failed before the agent ran — safe to retry
            )

        try:
            reply = await self._reply_waiter.wait(correlation_id, self._timeout_seconds)
            self._logger.info(
                "Received technical agent reply",
                step_id=step.id,
                execute_by=step.execute_by,
                agent_id=agent_id,
                delegated_correlation_id=correlation_id,
                raw_reply=dict(reply),
            )
        except TimeoutError:
            self._logger.error(
                "Technical agent reply timed out",
                step_id=step.id,
                execute_by=step.execute_by,
                agent_id=agent_id,
                delegated_correlation_id=correlation_id,
                timeout_seconds=self._timeout_seconds,
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=(
                    f"Technical agent reply timed out after {self._timeout_seconds:.0f}s "
                    f"for step_id='{step.id}' correlation_id='{correlation_id}'"
                ),
                # Idempotency guard: only retry a delegated timeout when explicitly enabled.
                retryable=self._retry_delegated_on_timeout,
            )
        except Exception as exc:
            self._logger.error(
                "Technical agent reply failed",
                step_id=step.id,
                execute_by=step.execute_by,
                agent_id=agent_id,
                delegated_correlation_id=correlation_id,
                error=str(exc),
            )
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=f"Technical agent reply failed: {exc}",
                retryable=True,  # unexpected transient failure while awaiting the reply
            )

        normalized = self._normalize_reply(reply)
        self._logger.info(
            "Normalized technical agent reply",
            step_id=step.id,
            execute_by=step.execute_by,
            agent_id=agent_id,
            delegated_correlation_id=correlation_id,
            output_message=self._extract_output_message(normalized.output_data),
            output_data=dict(normalized.output_data or {}),
            error=normalized.error,
            status=(StepStatus.ERROR.value if normalized.error else StepStatus.SUCCESS.value),
        )
        if normalized.error:
            return StepResult(
                step_id=step.id,
                status=StepStatus.ERROR,
                error=normalized.error,
            )

        return StepResult(
            step_id=step.id,
            status=StepStatus.SUCCESS,
            output=normalized.output_data,
        )

    def _build_conversation_metadata(
        self,
        request: DispatchStepRequest,
    ) -> dict[str, Any]:
        uploaded_file_ids = [
            file_id
            for file_id in request.step.uploaded_file_ids
            if isinstance(file_id, str) and file_id
        ]
        if not uploaded_file_ids:
            return {}

        return {"uploaded_file_ids": uploaded_file_ids}

    def _build_executor_metadata(
        self,
        request: DispatchStepRequest,
        correlation_id: str,
    ) -> dict[str, Any]:
        step = request.step
        metadata: dict[str, Any] = {
            "executor_correlation_id": request.correlation_id,
            "executor_message_id": request.message_id,
            "executor_conv_id": request.conv_id,
            "executor_batch_id": request.batch_id,
            "executor_step_id": step.id,
            "delegated_correlation_id": correlation_id,
        }
        metadata.update(self._extract_workflow_context(step.input_data))
        return metadata

    def _build_context_snapshot(
        self,
        request: DispatchStepRequest,
        capability: ResolvedCapability,
        executor_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        step = request.step
        return {
            "step": {
                "id": step.id,
                "execute_by": step.execute_by,
                "goal": step.goal,
                "message": step.message,
                "input_data": dict(step.input_data or {}),
                "output_data": dict(step.output_data or {}),
            },
            "capability": {
                "capability_id": capability.capability_id,
                "agent_type": capability.agent_type,
                "execution_target": capability.execution_target.value,
                "queue_metadata": dict(capability.queue_metadata or {}),
            },
            "request": {
                "message_id": request.message_id,
                "correlation_id": request.correlation_id,
                "conv_id": request.conv_id,
                "batch_id": request.batch_id,
                "from_agent": request.from_agent,
            },
            "executor_metadata": executor_metadata,
        }

    def _normalize_reply(self, reply: dict[str, Any]) -> NormalizedAgentReply:
        nested_payload = self._coerce_mapping(reply.get("payload"))
        step_payload = self._coerce_mapping(nested_payload.get("step"))
        status = str(
            step_payload.get("status")
            or reply.get("status")
            or reply.get("state")
            or ""
        ).lower()
        error = step_payload.get("error") or reply.get("error")
        output_data = self._coerce_mapping(
            step_payload.get("output_data")
            or reply.get("output_data")
            or reply.get("output")
            or reply.get("result")
        )

        if status in {"processing", "running", "in_progress", "interrupted"}:
            return NormalizedAgentReply(
                output_data=output_data,
                error=f"Technical agent reported non-terminal status '{status}'",
                raw_reply=reply,
            )

        if status in {"failed", "error"}:
            return NormalizedAgentReply(
                output_data=output_data,
                error=self._stringify_error(error)
                or "Technical agent reported failure",
                raw_reply=reply,
            )

        if error:
            return NormalizedAgentReply(
                output_data=output_data,
                error=self._stringify_error(error),
                raw_reply=reply,
            )

        if status and status not in {"success", "completed"}:
            return NormalizedAgentReply(
                output_data=output_data,
                error=f"Technical agent returned unsupported status '{status}'",
                raw_reply=reply,
            )

        return NormalizedAgentReply(
            output_data=output_data,
            error=None,
            raw_reply=reply,
        )

    def _extract_workflow_context(self, input_data: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "workflow_id",
            "workflow_run_id",
            "run_id",
            "task_run_id",
            "execution_run_id",
        )
        context: dict[str, Any] = {}
        for key in keys:
            value = input_data.get(key)
            if isinstance(value, str) and value:
                context[key] = value
        return context

    def _parse_agent_id(self, execute_by: str) -> str:
        prefix, _, agent_id = execute_by.partition(".")
        if prefix != "agent" or not agent_id:
            return ""
        return agent_id

    def _coerce_mapping(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {"value": value} if value is not None else {}

    def _stringify_error(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            message = value.get("message") or value.get("detail") or value.get("error")
            if isinstance(message, str) and message:
                return message
            return str(value)
        return str(value)

    def _build_message_overrides(
        self,
        *,
        request: DispatchStepRequest,
        step: Step,
        agent_id: str,
        message_id: str,
    ) -> TechnicalAgentMessageOverridesDto:
        uploaded_file_ids = [
            file_id
            for file_id in step.uploaded_file_ids
            if isinstance(file_id, str) and file_id
        ]
        step_payload = TechnicalAgentStepPayloadDto(
            id=step.id,
            execute_by=step.execute_by,
            goal=step.goal,
            message=step.message,
            uploaded_file_ids=uploaded_file_ids,
            input_data=dict(step.input_data or {}),
            output_data=dict(step.output_data or {}),
        )
        return TechnicalAgentMessageOverridesDto(
            message_id=message_id,
            conv_id=request.conv_id,
            conversation_id=request.conv_id,
            payload=TechnicalAgentMessagePayloadDto(
                from_agent="executor.runtime",
                to_agent=agent_id,
                type="step",
                step=step_payload,
            ),
        )

    def _resolve_request_message_type(self, capability: ResolvedCapability) -> str:
        return self._resolve_queue_metadata_string(
            capability,
            "request_message_type",
            settings.DELEGATION_REQUEST_MESSAGE_TYPE,
        )

    def _resolve_response_message_type(self, capability: ResolvedCapability) -> str:
        return self._resolve_queue_metadata_string(
            capability,
            "response_message_type",
            settings.DELEGATION_RESPONSE_MESSAGE_TYPE,
        )

    def _resolve_queue_metadata_string(
        self,
        capability: ResolvedCapability,
        key: str,
        default: str,
    ) -> str:
        value = capability.queue_metadata.get(key)
        if isinstance(value, str) and value:
            return value
        return default

    def _extract_output_message(self, output_data: dict[str, Any]) -> str:
        message = output_data.get("message") or output_data.get("result")
        if isinstance(message, str):
            return message
        return ""
