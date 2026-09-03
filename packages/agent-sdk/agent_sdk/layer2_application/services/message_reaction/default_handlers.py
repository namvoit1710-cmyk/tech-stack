from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer1_domain.value_objects.transport_source import TransportSource
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentInput,
    ResumeAgentOutput,
)

_EXECUTE_INPUT_ENVELOPE_KEYS = {
    "message",
    "execution_context",
    "intent",
    "session_id",
    "user_id",
    "tenant_id",
    "source",
    "correlation_id",
    "metadata",
    "uploaded_file_ids",
    "reply_to",
    "reply_topic",
    "agent_type",
    "context_snapshot",
    "parameters",
}
EXECUTE_MESSAGE_TYPES = frozenset({"agent.request.agent", "executor.request.agent"})
DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE = "agent.response"
_EXECUTOR_STEP_REQUEST_TYPE = "executor.request.agent"
_EXECUTOR_STEP_STATUS_TYPE = "agent.step.status"
_EXECUTOR_STEP_CONTRACT_KEY = "executor_step_contract"
_EXECUTOR_STEP_REQUEST_CONTRACT = "executor_step"

_SDK_TRANSPORT_SOURCE_KEY = "_sdk_transport_source"


@dataclass(frozen=True)
class ExecutorStepOutputDataDto:
    message: str = ""
    status: str = "success"
    agent_data: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_output(
        cls,
        output: ExecuteAgentOutput | ResumeAgentOutput,
    ) -> "ExecutorStepOutputDataDto":
        output_data = getattr(output, "data", None)
        return cls(
            message=output.message,
            status=output.status,
            agent_data=dict(output.agent_data or {}),
            data=dict(output_data or {}) if isinstance(output_data, dict) else {},
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "message": self.message,
            "status": self.status,
            "agent_data": dict(self.agent_data or {}),
        }
        if self.data:
            payload["data"] = dict(self.data)
        return payload


def _read_mapping_string(value: Any, *keys: str) -> str:
    if not isinstance(value, Mapping):
        return ""
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return ""


def _resolve_response_message_type_from_request(
    request: Any,
    *,
    default: str,
) -> str:
    execution_context = getattr(request, "execution_context", {}) or {}
    response_message_type = _read_mapping_string(
        execution_context,
        "response_message_type",
        "response_type",
    )
    return response_message_type or default


def _normalize_source(value: str) -> str:
    return TransportSource.normalize(value, default="")


def _resolve_source(
    raw: dict[str, Any],
    *,
    nested_input: dict[str, Any] | None = None,
    execution_context: dict[str, Any] | None = None,
    default: str = "broker",
) -> str:
    nested_input = nested_input or {}
    execution_context = execution_context or {}

    source = (
        _read_mapping_string(raw, "source", "transport_source", "messaging_source")
        or _read_mapping_string(
            nested_input, "source", "transport_source", "messaging_source"
        )
        or _read_mapping_string(
            execution_context, "source", "transport_source", "messaging_source"
        )
        or _read_mapping_string(
            raw,
            _SDK_TRANSPORT_SOURCE_KEY,
            "_sdk_transport",
            "transport",
            "backend_name",
            "messaging_backend",
        )
        or _read_mapping_string(
            nested_input,
            _SDK_TRANSPORT_SOURCE_KEY,
            "_sdk_transport",
            "transport",
            "backend_name",
            "messaging_backend",
        )
        or _read_mapping_string(
            raw.get("metadata"), "source", "transport_source", "messaging_source"
        )
        or _read_mapping_string(
            nested_input.get("metadata"),
            "source",
            "transport_source",
            "messaging_source",
        )
        or default
    )
    return _normalize_source(source)


def _merge_execute_parameters(
    nested_input: dict[str, Any], parameters: Any
) -> dict[str, Any]:
    merged_parameters = dict(parameters) if isinstance(parameters, dict) else {}
    for key, value in nested_input.items():
        if key == _SDK_TRANSPORT_SOURCE_KEY:
            continue
        if key in _EXECUTE_INPUT_ENVELOPE_KEYS:
            continue
        merged_parameters.setdefault(key, value)
    return merged_parameters


def _extract_executor_step_contract(raw: dict[str, Any]) -> dict[str, Any] | None:
    if raw.get("type") != _EXECUTOR_STEP_REQUEST_TYPE:
        return None
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("executor.request.agent payload must be an object")
    step = payload.get("step")
    if not isinstance(step, Mapping):
        raise ValueError("executor.request.agent payload.step must be an object")
    return {
        "from": str(payload.get("from") or ""),
        "to": str(payload.get("to") or ""),
        "type": str(payload.get("type") or "step"),
        "step": dict(step),
    }


def _copy_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _build_executor_step_execute_input(raw: dict[str, Any]) -> ExecuteAgentInput:
    contract = _extract_executor_step_contract(raw)
    if contract is None:
        raise ValueError("executor step contract was not present")
    step = contract["step"]
    execution_context = dict(raw.get("execution_context") or {})
    response_message_type = _read_mapping_string(
        raw, "response_message_type", "response_type"
    ) or _read_mapping_string(
        execution_context, "response_message_type", "response_type"
    )
    if response_message_type:
        execution_context["response_message_type"] = response_message_type
    conv_id = str(raw.get("conversation_id") or raw.get("conv_id") or "")
    execution_context.setdefault("conversation_id", conv_id)
    execution_context["request_contract_type"] = _EXECUTOR_STEP_REQUEST_CONTRACT
    execution_context[_EXECUTOR_STEP_CONTRACT_KEY] = contract
    execute_input_kwargs: dict[str, Any] = {
        "message": str(step.get("message") or ""),
        "conv_id": conv_id,
        "session_id": str(raw.get("session_id") or ""),
        "user_id": str(raw.get("user_id") or "anonymous"),
        "tenant_id": str(raw.get("tenant_id") or "default"),
        "source": _resolve_source(raw, execution_context=execution_context),
        "correlation_id": raw.get("correlation_id", ""),
        "parameters": dict(step.get("input_data") or {}),
        "metadata": ConversationMetadata(**(raw.get("metadata") or {})),
        "uploaded_file_ids": _copy_string_list(step.get("uploaded_file_ids")),
        "reply_to": raw.get("reply_to"),
        "reply_topic": raw.get("reply_topic"),
        "action": raw.get("action"),
        "agent_type": str(step.get("execute_by") or "") or None,
        "intent": raw.get("intent") if isinstance(raw.get("intent"), dict) else {},
        "execution_context": execution_context,
        "context_snapshot": raw.get("context_snapshot") or {},
    }
    return ExecuteAgentInput(**execute_input_kwargs)


def _build_executor_step_response(
    output: ExecuteAgentOutput | ResumeAgentOutput,
    *,
    request: ExecuteAgentInput,
    request_correlation_id: str,
    response_message_type: str = _EXECUTOR_STEP_STATUS_TYPE,
) -> dict[str, Any]:
    contract = request.execution_context.get(_EXECUTOR_STEP_CONTRACT_KEY) or {}
    if not isinstance(contract, Mapping):
        contract = {}
    step = contract.get("step") or {}
    if not isinstance(step, Mapping):
        step = {}
    step_payload = dict(step)
    step_payload["status"] = output.status
    step_payload["error"] = output.error or ""
    step_payload["output_data"] = _build_executor_step_output_data(output)
    return {
        "type": str(response_message_type or _EXECUTOR_STEP_STATUS_TYPE).strip()
        or _EXECUTOR_STEP_STATUS_TYPE,
        "message_id": str(uuid4()),
        "correlation_id": request_correlation_id
        or output.correlation_id
        or request.correlation_id
        or "",
        "conversation_id": request.conv_id,
        "payload": {
            "from": str(contract.get("to") or request.agent_type or ""),
            "to": str(contract.get("from") or ""),
            "type": str(contract.get("type") or "step"),
            "step": step_payload,
        },
    }


def _build_executor_step_output_data(
    output: ExecuteAgentOutput | ResumeAgentOutput,
) -> dict[str, Any]:
    return ExecutorStepOutputDataDto.from_output(output).to_dict()


def _coerce_uploaded_file_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    return value or []


def build_execute_input(raw: dict[str, Any]) -> ExecuteAgentInput:
    if raw.get("type") == _EXECUTOR_STEP_REQUEST_TYPE:
        return _build_executor_step_execute_input(raw)

    nested_input: dict[str, Any] = raw.get("input") or {}
    execution_context: dict[str, Any] = dict(
        raw.get("execution_context") or nested_input.get("execution_context") or {}
    )
    response_message_type = (
        _read_mapping_string(raw, "response_message_type", "response_type")
        or _read_mapping_string(nested_input, "response_message_type", "response_type")
        or _read_mapping_string(
            execution_context, "response_message_type", "response_type"
        )
    )
    if response_message_type:
        execution_context["response_message_type"] = response_message_type
    conv_id: str = (
        raw.get("conv_id")
        or raw.get("conversation_id")
        or execution_context.get("conversation_id", "")
    ) or ""
    intent: dict[str, Any] = raw.get("intent") or nested_input.get("intent") or {}
    message: str = (
        raw.get("message") or nested_input.get("message") or intent.get("text", "")
    )
    parameters = _merge_execute_parameters(
        nested_input,
        raw.get("parameters") or nested_input.get("parameters") or {},
    )
    execute_input_kwargs: dict[str, Any] = {
        "message": message,
        "conv_id": conv_id,
        "session_id": raw.get("session_id")
        or nested_input.get("session_id")
        or execution_context.get("session_id", ""),
        "user_id": raw.get("user_id")
        or nested_input.get("user_id")
        or execution_context.get("user_id", "anonymous"),
        "tenant_id": raw.get("tenant_id")
        or nested_input.get("tenant_id")
        or execution_context.get("tenant_id", "default"),
        "source": _resolve_source(
            raw,
            nested_input=nested_input,
            execution_context=execution_context,
        ),
        "correlation_id": raw.get("correlation_id", ""),
        "parameters": parameters,
        "metadata": ConversationMetadata(
            **((raw.get("metadata") or nested_input.get("metadata")) or {})
        ),
        "uploaded_file_ids": _coerce_uploaded_file_ids(
            raw.get("uploaded_file_ids") or nested_input.get("uploaded_file_ids")
        ),
        "reply_to": raw.get("reply_to") or nested_input.get("reply_to"),
        "reply_topic": raw.get("reply_topic"),
        "action": raw.get("action") or nested_input.get("action"),
        "agent_type": raw.get("agent_type"),
        "intent": intent,
        "execution_context": execution_context,
        "context_snapshot": raw.get("context_snapshot") or {},
    }
    return ExecuteAgentInput(**execute_input_kwargs)


def build_resume_input(raw: dict[str, Any], *, thread_id: str) -> ResumeAgentInput:
    resume_input_kwargs: dict[str, Any] = {
        "thread_id": thread_id,
        "resume_value": raw.get("result")
        if "result" in raw
        else raw.get("resume_value"),
        "interrupt_id": raw.get("interrupt_id"),
        "correlation_id": raw.get("correlation_id"),
        "metadata": ConversationMetadata(**(raw.get("metadata") or {})),
        "uploaded_file_ids": _coerce_uploaded_file_ids(raw.get("uploaded_file_ids")),
        "reply_to": raw.get("reply_to"),
        "reply_topic": raw.get("reply_topic"),
    }
    return ResumeAgentInput(**resume_input_kwargs)


def build_agent_response(
    output: ExecuteAgentOutput | ResumeAgentOutput,
    *,
    request_correlation_id: str,
    parent_thread_id: str | None = None,
    response_message_type: str = DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE,
) -> dict[str, Any]:
    interrupt_payload = output.interrupt_payload
    if interrupt_payload is not None and hasattr(interrupt_payload, "__dict__"):
        serialized_interrupt = interrupt_payload.__dict__.copy()
    else:
        serialized_interrupt = interrupt_payload
    output_session_id = getattr(output, "session_id", "")
    related_step_id = getattr(output, "related_step_id", None)
    is_critical = getattr(output, "is_critical", False)
    error_context = getattr(output, "error_context", None)
    response = {
        "type": str(
            response_message_type or DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE
        ).strip()
        or DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE,
        "correlation_id": output.correlation_id,
        "success": output.status != "error",
        "message": output.message,
        "result": {
            "message": output.message,
            "status": output.status,
            "session_id": output_session_id,
            "interrupted": output.interrupted,
            "interrupt_payload": serialized_interrupt,
            "agent_data": output.agent_data,
            "error": output.error,
            "error_code": output.error_code,
            "related_step_id": related_step_id,
            "is_critical": is_critical,
            "error_context": error_context,
        },
        "error": output.error,
        "agent_data": output.agent_data,
        "status": output.status,
    }
    if output.interrupted and output.interrupt_payload:
        response["interrupted"] = True
        response["correlation_id"] = request_correlation_id
    elif request_correlation_id and not response["correlation_id"]:
        response["correlation_id"] = request_correlation_id
    if parent_thread_id:
        response["delegation"] = {"parent_thread_id": parent_thread_id}
    return response


def build_execute_response(
    output: ExecuteAgentOutput | ResumeAgentOutput,
    *,
    request_correlation_id: str,
    parent_thread_id: str | None = None,
    request: ExecuteAgentInput | None = None,
) -> dict[str, Any]:
    if (
        request is not None
        and request.execution_context.get("request_contract_type")
        == _EXECUTOR_STEP_REQUEST_CONTRACT
    ):
        return _build_executor_step_response(
            output,
            request=request,
            request_correlation_id=request_correlation_id,
            response_message_type=_resolve_response_message_type_from_request(
                request,
                default=_EXECUTOR_STEP_STATUS_TYPE,
            ),
        )
    response_message_type = DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE
    if request is not None:
        response_message_type = _resolve_response_message_type_from_request(
            request,
            default=DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE,
        )
    return build_agent_response(
        output,
        request_correlation_id=request_correlation_id,
        parent_thread_id=parent_thread_id,
        response_message_type=response_message_type,
    )
