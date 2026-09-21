from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, Optional

from agent_sdk.layer1_domain.entities.agent_request import RequestContext
from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import HitlInterruptPayload
from agent_sdk.layer1_domain.entities.orchestration_events import (
    create_agent_plan_error_event,
)
from agent_sdk.layer1_domain.entities.tenant_context import TenantContext
from agent_sdk.layer1_domain.exceptions import AgentSDKError, NodeExecutionError
from agent_sdk.layer2_application.features.execute_agent.use_cases.output_normalizer import (
    normalize_final_state,
)
from agent_sdk.layer2_application.interfaces.observability import ILogger, IMonitor


def _extract_graph_interrupt(exc: BaseException) -> Any:
    if type(exc).__name__ == "GraphInterrupt":
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            if type(child).__name__ == "GraphInterrupt":
                return child
    return None


def _normalize_graph_state_value(value: Any) -> Any:
    if is_dataclass(value):
        return _normalize_graph_state_value(asdict(value))
    if isinstance(value, dict):
        return {key: _normalize_graph_state_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_graph_state_value(item) for item in value]
    return value


def _coerce_conversation_metadata(value: Any) -> ConversationMetadata:
    if isinstance(value, ConversationMetadata):
        return value
    if isinstance(value, dict):
        return ConversationMetadata(**value)
    return ConversationMetadata()


def _normalize_uploaded_file_ids(value: Any) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    if isinstance(value, str):
        value = [value]
    for raw in value or []:
        file_id = str(raw).strip()
        if not file_id or file_id in seen:
            continue
        normalized.append(file_id)
        seen.add(file_id)
    return normalized


def _normalize_conversation_context(
    conv_id: str,
    metadata_value: Any,
    uploaded_file_ids_value: Any,
) -> tuple[ConversationMetadata, list[str]]:
    metadata = _coerce_conversation_metadata(metadata_value)
    normalized_uploaded_file_ids = _normalize_uploaded_file_ids(uploaded_file_ids_value)
    normalized_metadata_uploaded_file_ids = _normalize_uploaded_file_ids(
        metadata.uploaded_file_ids
    )

    if normalized_uploaded_file_ids and not normalized_metadata_uploaded_file_ids:
        normalized_metadata_uploaded_file_ids = list(normalized_uploaded_file_ids)
    elif normalized_metadata_uploaded_file_ids and not normalized_uploaded_file_ids:
        normalized_uploaded_file_ids = list(normalized_metadata_uploaded_file_ids)
    elif (
        normalized_uploaded_file_ids
        and normalized_metadata_uploaded_file_ids
        and normalized_uploaded_file_ids != normalized_metadata_uploaded_file_ids
    ):
        raise ValueError("uploaded_file_ids mismatch between request and metadata")

    metadata = ConversationMetadata(
        main_conv_id=metadata.main_conv_id,
        sub_conv_ids=list(metadata.sub_conv_ids),
        uploaded_file_ids=normalized_metadata_uploaded_file_ids,
    )

    has_routing_metadata = bool(metadata.main_conv_id or metadata.sub_conv_ids)
    if has_routing_metadata:
        if not conv_id.startswith(("main_", "sub_")):
            raise ValueError("conv_id must start with 'main_' or 'sub_'")
        if conv_id.startswith("main_") and conv_id != metadata.main_conv_id:
            raise ValueError(
                "main conversation conv_id must match metadata.main_conv_id"
            )
        if conv_id.startswith("sub_") and conv_id not in metadata.sub_conv_ids:
            raise ValueError(
                "sub conversation conv_id must exist in metadata.sub_conv_ids"
            )

    return metadata, normalized_uploaded_file_ids


def _serialize_error_context_from_exception(exc: Exception) -> dict[str, Any] | None:
    if isinstance(exc, NodeExecutionError) and isinstance(exc.error_context, dict):
        return dict(exc.error_context)
    if isinstance(exc, AgentSDKError):
        return exc.to_error_context()
    return None


def _build_semantic_error_payload(exc: Exception, fallback_code: str) -> dict[str, Any]:
    error_context = _serialize_error_context_from_exception(exc)
    message = str(exc)
    error_code = fallback_code
    related_step_id = None
    is_critical = False

    if error_context is not None:
        message = error_context.get("message") or message
        error_code = error_context.get("error_code") or fallback_code
        related_step_id = error_context.get("related_step_id")
        is_critical = bool(
            error_context.get("is_critical", error_context.get("critical"))
        )
    elif isinstance(exc, AgentSDKError):
        error_code = exc.error_code or fallback_code
        related_step_id = exc.related_step_id
        is_critical = exc.is_critical

    return {
        "message": message,
        "error": message,
        "error_code": error_code,
        "related_step_id": related_step_id,
        "is_critical": is_critical,
        "error_context": error_context,
    }


@dataclass
class ExecuteAgentInput:
    message: str
    conv_id: str = ""
    session_id: str = ""
    user_id: str = "anonymous"
    tenant_id: str = "default"
    source: str = "api"
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Optional[RequestContext] = None
    metadata: ConversationMetadata = field(default_factory=ConversationMetadata)
    uploaded_file_ids: list[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    reply_topic: Optional[str] = None
    action: Optional[str] = None
    agent_type: Optional[str] = None
    intent: Dict[str, Any] = field(default_factory=dict)
    execution_context: Dict[str, Any] = field(default_factory=dict)
    context_snapshot: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecuteAgentOutput:
    message: str = ""
    status: str = "success"
    session_id: str = ""
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


class ExecuteAgentUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        agent_graph: Any = None,
        workflow_event_emitter: Any = None,
        default_tenant_id: str = "default",
        **kwargs,
    ) -> None:
        self.logger = logger
        self.monitor = monitor
        self.graph = agent_graph
        self._event_emitter = workflow_event_emitter
        self._default_tenant_id = default_tenant_id

    def _invalid_metadata_output(
        self, request: ExecuteAgentInput, exc: ValueError
    ) -> ExecuteAgentOutput:
        self.logger.error(
            "Invalid conversation metadata",
            error=str(exc),
            conv_id=request.conv_id,
        )
        self.monitor.track("agent_execute_error", 1)
        return ExecuteAgentOutput(
            message="",
            error=str(exc),
            error_code="INVALID_CONVERSATION_METADATA",
            status="error",
        )

    def _prepare_request(self, request: ExecuteAgentInput) -> ExecuteAgentOutput | None:
        try:
            metadata, uploaded_file_ids = _normalize_conversation_context(
                request.conv_id,
                request.metadata,
                request.uploaded_file_ids,
            )
        except ValueError as exc:
            return self._invalid_metadata_output(request, exc)

        request.metadata = metadata
        request.uploaded_file_ids = uploaded_file_ids
        return None

    def _build_graph_config(self, request: ExecuteAgentInput) -> dict[str, Any]:
        return {
            "configurable": {
                "thread_id": request.conv_id,
                "conv_id": request.conv_id,
                "correlation_id": request.correlation_id,
                "metadata": _normalize_graph_state_value(request.metadata),
                "uploaded_file_ids": list(request.uploaded_file_ids),
            }
        }

    async def execute(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        self.logger.info(
            "Agent execution started",
            request=request,
        )
        self.monitor.track("agent_execute_requests", 1)
        session_id = request.session_id if request.session_id else str(uuid.uuid4())
        if not request.conv_id:
            request.conv_id = session_id
        start = time.monotonic()

        invalid_result = self._prepare_request(request)
        if invalid_result is not None:
            invalid_result.duration_ms = (time.monotonic() - start) * 1000
            invalid_result.correlation_id = request.correlation_id
            invalid_result.session_id = session_id
            return invalid_result

        if self._event_emitter is not None:
            from agent_sdk.layer2_application.services.workflow_event_runtime import (
                workflow_event_scope,
            )

            with workflow_event_scope(
                self._event_emitter, request=request, mode="execute"
            ):
                result = await self._execute_core(request, session_id=session_id)
        else:
            result = await self._execute_core(request, session_id=session_id)

        result.duration_ms = (time.monotonic() - start) * 1000
        result.correlation_id = request.correlation_id
        result.session_id = session_id
        return result

    async def _execute_core(
        self, request: ExecuteAgentInput, *, session_id: str
    ) -> ExecuteAgentOutput:
        if self.graph is not None:
            return await self._execute_via_graph(request, session_id=session_id)
        return self._execute_echo(request)

    async def _execute_via_graph(
        self, request: ExecuteAgentInput, *, session_id: str
    ) -> ExecuteAgentOutput:
        tenant_context = self._build_tenant_context(request)
        initial_state = self._build_initial_state(
            request=request,
            session_id=session_id,
            tenant_context=tenant_context,
        )
        try:
            config = self._build_graph_config(request)
            final_state: Dict[str, Any] = await self.graph.ainvoke(
                initial_state, config=config
            )
            if final_state.get("__interrupt__"):
                return self._handle_state_interrupt(
                    final_state, request, tenant_context
                )
            normalized = normalize_final_state(final_state)
            if normalized.get("error"):
                self.monitor.track("agent_execute_error", 1)
            elif normalized.get("status") == "success" and not normalized.get(
                "agent_data"
            ):
                self.monitor.track("agent_execute_success", 1)
            else:
                self.monitor.track("agent_execute_success_with_format", 1)
            normalized_for_execute = {
                k: v for k, v in normalized.items() if k != "data"
            }
            return ExecuteAgentOutput(**normalized_for_execute)
        except BaseException as exc:
            interrupt = _extract_graph_interrupt(exc)
            if interrupt is not None:
                return self._handle_graph_interrupt(interrupt, request, tenant_context)
            if isinstance(exc, Exception):
                error_payload = _build_semantic_error_payload(exc, "AGENT_GRAPH_ERROR")
                self.logger.error(
                    "Graph execution failed",
                    error=error_payload["message"],
                    error_code=error_payload["error_code"],
                    related_step_id=error_payload["related_step_id"],
                    is_critical=error_payload["is_critical"],
                    conv_id=request.conv_id,
                )
                self.monitor.track("agent_execute_error", 1)
                if self._event_emitter is not None:
                    await self._event_emitter.emit_orchestration_event(
                        create_agent_plan_error_event(
                            request.conv_id,
                            {
                                "message": error_payload["message"],
                                "error_code": error_payload["error_code"],
                                "related_step_id": error_payload["related_step_id"],
                                "is_critical": error_payload["is_critical"],
                                "error_context": error_payload["error_context"],
                            },
                        )
                    )
                return ExecuteAgentOutput(
                    message="",
                    error=error_payload["message"],
                    error_code=error_payload["error_code"],
                    status="error",
                    related_step_id=error_payload["related_step_id"],
                    is_critical=error_payload["is_critical"],
                    error_context=error_payload["error_context"],
                )
            raise

    def _resolve_tenant_id(self, request: ExecuteAgentInput) -> str:
        tenant_id = request.tenant_id.strip() if request.tenant_id else ""
        if not tenant_id:
            tenant_id = self._default_tenant_id
        return tenant_id

    def _build_tenant_context(self, request: ExecuteAgentInput) -> TenantContext:
        return TenantContext(
            tenant_id=self._resolve_tenant_id(request),
            user_id=request.user_id,
            conv_id=request.conv_id,
            source=request.source,
            correlation_id=request.correlation_id,
        )

    def _build_initial_state(
        self,
        *,
        request: ExecuteAgentInput,
        session_id: str,
        tenant_context: TenantContext,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        tenant_id = tenant_context.tenant_id
        return {
            "message": request.message,
            "conv_id": request.conv_id,
            "session_id": session_id,
            "user_id": request.user_id,
            "tenant_id": tenant_id,
            "source": request.source,
            "correlation_id": request.correlation_id,
            "transport_state": "IDLE",
            "parameters": dict(
                parameters if parameters is not None else (request.parameters or {})
            ),
            "metadata": _normalize_graph_state_value(request.metadata),
            "uploaded_file_ids": list(request.uploaded_file_ids),
            "execution_context": dict(request.execution_context or {}),
            "context_snapshot": dict(request.context_snapshot or {}),
            "tenant_context": _normalize_graph_state_value(tenant_context),
            "context": _normalize_graph_state_value(request.context),
        }

    def _handle_state_interrupt(
        self,
        final_state: Dict[str, Any],
        request: "ExecuteAgentInput",
        tenant_context: "TenantContext",
    ) -> "ExecuteAgentOutput":
        self.logger.info(
            "Graph execution interrupted (HITL pause via state __interrupt__)",
            conv_id=request.conv_id,
        )
        self.monitor.track("agent_execute_interrupted", 1)

        interrupts = final_state.get("__interrupt__", [])
        first = interrupts[0] if interrupts else None

        payload: Optional[HitlInterruptPayload] = None
        if first is not None:
            payload = HitlInterruptPayload(
                thread_id=request.conv_id,
                interrupt_id=first.id,
                value=first.value,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
                conv_id=request.conv_id,
            )

        return ExecuteAgentOutput(
            status="interrupted",
            interrupted=True,
            interrupt_payload=payload,
        )

    def _handle_graph_interrupt(
        self,
        exc: Any,
        request: ExecuteAgentInput,
        tenant_context: TenantContext,
    ) -> ExecuteAgentOutput:
        self.logger.info(
            "Graph execution interrupted (HITL pause)",
            conv_id=request.conv_id,
        )
        self.monitor.track("agent_execute_interrupted", 1)

        interrupts = exc.args[0] if exc.args else []
        first = interrupts[0] if interrupts else None

        payload: Optional[HitlInterruptPayload] = None
        if first is not None:
            payload = HitlInterruptPayload(
                thread_id=request.conv_id,
                interrupt_id=first.id,
                value=first.value,
                tenant_id=tenant_context.tenant_id,
                user_id=tenant_context.user_id,
                conv_id=request.conv_id,
            )

        return ExecuteAgentOutput(
            status="interrupted",
            interrupted=True,
            interrupt_payload=payload,
        )

    def _execute_echo(self, request: ExecuteAgentInput) -> ExecuteAgentOutput:
        self.logger.warning(
            "No agent graph registered; returning echo response",
            conv_id=request.conv_id,
        )
        self.monitor.track("agent_execute_echo", 1)
        return ExecuteAgentOutput(message=f"[echo] {request.message}", status="success")
