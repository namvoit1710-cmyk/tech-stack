from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
    HitlInterruptPayload,
    InterruptType,
)
from agent_sdk.layer1_domain.entities.orchestration_events import (
    create_agent_plan_error_event,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    _build_semantic_error_payload,
    _normalize_conversation_context,
    _normalize_graph_state_value,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.output_normalizer import (
    normalize_final_state,
)
from agent_sdk.layer2_application.interfaces.observability import ILogger, IMonitor

_SAFE_STATE_KEYS = frozenset({"message", "conv_id", "session_id", "formatted_response"})


def _parse_interrupt_type(raw: str) -> InterruptType:
    try:
        return InterruptType(raw.upper())
    except (ValueError, AttributeError):
        return InterruptType.GENERIC


@dataclass
class ResumeAgentInput:
    thread_id: str
    resume_value: Any
    interrupt_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: ConversationMetadata = field(default_factory=ConversationMetadata)
    uploaded_file_ids: list[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    reply_topic: Optional[str] = None


@dataclass
class ResumeAgentOutput:
    message: str = ""
    status: str = "success"
    data: Dict[str, Any] = field(default_factory=dict)
    agent_data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    correlation_id: Optional[str] = None
    duration_ms: float = 0.0
    interrupted: bool = False
    interrupt_payload: Optional[HitlInterruptPayload] = None
    related_step_id: Optional[str] = None
    is_critical: bool = False
    error_context: Optional[Dict[str, Any]] = None


class ResumeAgentUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        agent_graph: Any = None,
        workflow_event_emitter: Any = None,
        agent_runtime: Any = None,
        **kwargs,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self.graph = agent_graph
        self._agent_runtime = agent_runtime
        self._event_emitter = workflow_event_emitter

    def _invalid_metadata_output(
        self, request: ResumeAgentInput, exc: ValueError
    ) -> ResumeAgentOutput:
        self.logger.error(
            "Invalid conversation metadata",
            error=str(exc),
            thread_id=request.thread_id,
        )
        self.monitor.track("agent_resume_error", 1)
        return ResumeAgentOutput(
            message="",
            error=str(exc),
            error_code="INVALID_CONVERSATION_METADATA",
            status="error",
            correlation_id=request.correlation_id,
        )

    def _prepare_request(self, request: ResumeAgentInput) -> ResumeAgentOutput | None:
        try:
            metadata, uploaded_file_ids = _normalize_conversation_context(
                request.thread_id,
                request.metadata,
                request.uploaded_file_ids,
            )
        except ValueError as exc:
            return self._invalid_metadata_output(request, exc)

        request.metadata = metadata
        request.uploaded_file_ids = uploaded_file_ids
        return None

    def _build_graph_config(self, request: ResumeAgentInput) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": request.thread_id,
                "conv_id": request.thread_id,
                "correlation_id": request.correlation_id,
                "metadata": _normalize_graph_state_value(request.metadata),
                "uploaded_file_ids": list(request.uploaded_file_ids),
            }
        }

    async def execute(self, request: ResumeAgentInput) -> ResumeAgentOutput:
        self.logger.info(
            "Agent resume started",
            request=request,
        )
        self.monitor.track("agent_resume_requests", 1)
        start = time.monotonic()

        invalid_result = self._prepare_request(request)
        if invalid_result is not None:
            invalid_result.duration_ms = (time.monotonic() - start) * 1000
            return invalid_result

        if self._event_emitter is not None:
            from agent_sdk.layer2_application.services.workflow_event_runtime import (
                workflow_event_scope,
            )

            with workflow_event_scope(
                self._event_emitter, request=request, mode="resume"
            ):
                result = await self._resume_via_graph(request)
        else:
            result = await self._resume_via_graph(request)

        result.duration_ms = (time.monotonic() - start) * 1000
        result.correlation_id = request.correlation_id
        return result

    async def _resume_via_graph(self, request: ResumeAgentInput) -> ResumeAgentOutput:
        config = self._build_graph_config(request)

        if self._agent_runtime is not None:
            try:
                runtime_result = await self._agent_runtime.resume(
                    request.resume_value,
                    config,
                    interrupt_id=request.interrupt_id,
                )
            except Exception as exc:
                error_payload = _build_semantic_error_payload(exc, "AGENT_RESUME_ERROR")
                self.logger.error(
                    "Graph resume failed",
                    error=error_payload["message"],
                    error_code=error_payload["error_code"],
                    related_step_id=error_payload["related_step_id"],
                    is_critical=error_payload["is_critical"],
                    thread_id=request.thread_id,
                )
                self.monitor.track("agent_resume_error", 1)
                if self._event_emitter is not None:
                    await self._event_emitter.emit_orchestration_event(
                        create_agent_plan_error_event(
                            request.thread_id,
                            {
                                "message": error_payload["message"],
                                "error_code": error_payload["error_code"],
                                "related_step_id": error_payload["related_step_id"],
                                "is_critical": error_payload["is_critical"],
                                "error_context": error_payload["error_context"],
                            },
                        )
                    )
                return ResumeAgentOutput(
                    message="",
                    error=error_payload["message"],
                    error_code=error_payload["error_code"],
                    status="error",
                    related_step_id=error_payload["related_step_id"],
                    is_critical=error_payload["is_critical"],
                    error_context=error_payload["error_context"],
                )

            if runtime_result.interrupted:
                interrupt = runtime_result.interrupt_payload
                payload: Optional[HitlInterruptPayload] = None
                if interrupt is not None:
                    exc = interrupt.payload
                    interrupts = exc.args[0] if exc.args else []
                    first = interrupts[0] if interrupts else None
                    if first is not None:
                        val = first.value
                        int_type = InterruptType.GENERIC
                        int_msg = ""
                        if isinstance(val, dict):
                            int_type = _parse_interrupt_type(val.get("type", "GENERIC"))
                            int_msg = val.get("message", "")
                        payload = HitlInterruptPayload(
                            thread_id=request.thread_id,
                            interrupt_id=first.id,
                            value=val,
                            type=int_type,
                            message=int_msg,
                            conv_id=request.thread_id,
                        )
                self.monitor.track("agent_resume_interrupted", 1)
                return ResumeAgentOutput(
                    status="interrupted",
                    interrupted=True,
                    interrupt_payload=payload,
                )
            final_state: Dict[str, Any] = runtime_result.output or {}
        else:
            self.logger.error(
                "Direct graph resume attempted but not supported. AgentRuntime is required.",
                thread_id=request.thread_id,
            )
            return ResumeAgentOutput(
                message="Resume failed: AgentRuntime is required for correct resume semantics.",
                status="error",
                error="AgentRuntime missing",
                error_code="AGENT_RUNTIME_MISSING",
            )

        if final_state.get("__interrupt__"):
            return self._handle_state_interrupt(final_state, request)
        normalized = normalize_final_state(final_state)
        if normalized.get("error"):
            self.monitor.track("agent_resume_error", 1)
        elif final_state.get("formatted_response"):
            self.monitor.track("agent_resume_success_with_format", 1)
        else:
            self.monitor.track("agent_resume_success", 1)
        return ResumeAgentOutput(
            message=normalized.get("message", ""),
            status=normalized.get("status", "success"),
            data=normalized.get("data", {}),
            agent_data=normalized.get("agent_data", {}),
            error=normalized.get("error"),
            error_code=normalized.get("error_code"),
            related_step_id=normalized.get("related_step_id"),
            is_critical=bool(normalized.get("is_critical", False)),
            error_context=normalized.get("error_context"),
        )

    def _handle_graph_interrupt(
        self,
        exc: Any,
        request: ResumeAgentInput,
    ) -> ResumeAgentOutput:
        self.logger.info(
            "Graph resume interrupted (another HITL pause)",
            thread_id=request.thread_id,
        )
        self.monitor.track("agent_resume_interrupted", 1)

        interrupts = exc.args[0] if exc.args else []
        first = interrupts[0] if interrupts else None

        payload: Optional[HitlInterruptPayload] = None
        if first is not None:
            val = first.value
            int_type = InterruptType.GENERIC
            int_msg = ""
            if isinstance(val, dict):
                int_type = _parse_interrupt_type(val.get("type", "GENERIC"))
                int_msg = val.get("message", "")

            payload = HitlInterruptPayload(
                thread_id=request.thread_id,
                interrupt_id=first.id,
                value=val,
                type=int_type,
                message=int_msg,
                conv_id=request.thread_id,
            )

        return ResumeAgentOutput(
            status="interrupted",
            interrupted=True,
            interrupt_payload=payload,
        )

    def _handle_state_interrupt(
        self,
        final_state: Dict[str, Any],
        request: ResumeAgentInput,
    ) -> ResumeAgentOutput:
        self.logger.info(
            "Graph resume interrupted (another HITL pause via state __interrupt__)",
            thread_id=request.thread_id,
        )
        self.monitor.track("agent_resume_interrupted", 1)

        interrupts = final_state.get("__interrupt__", [])
        first = interrupts[0] if interrupts else None

        payload: Optional[HitlInterruptPayload] = None
        if first is not None:
            val = first.value
            int_type = InterruptType.GENERIC
            int_msg = ""
            if isinstance(val, dict):
                int_type = _parse_interrupt_type(val.get("type", "GENERIC"))
                int_msg = val.get("message", "")

            payload = HitlInterruptPayload(
                thread_id=request.thread_id,
                interrupt_id=first.id,
                value=val,
                type=int_type,
                message=int_msg,
                conv_id=request.thread_id,
            )

        return ResumeAgentOutput(
            status="interrupted",
            interrupted=True,
            interrupt_payload=payload,
        )
