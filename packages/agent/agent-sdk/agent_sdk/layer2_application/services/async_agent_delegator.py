from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from agent_sdk.layer1_domain.entities.agent_call import AgentCallRequest
from agent_sdk.layer1_domain.exceptions import RegistrationError
from agent_sdk.layer2_application.services.correlation_thread_store import (
    CorrelationThreadStore,
)

DEFAULT_AGENT_REQUEST_MESSAGE_TYPE = "agent.request.agent"
DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE = "agent.response"


def _non_empty_string(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return ""


def _coerce_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return dict(asdict(value))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        return dict(dumped) if isinstance(dumped, dict) else {}

    dict_method = getattr(value, "dict", None)
    if callable(dict_method):
        dumped = dict_method()
        return dict(dumped) if isinstance(dumped, dict) else {}

    return {}


class AsyncAgentDelegator:
    def __init__(
        self,
        publisher: Any,
        registry: Any,
        correlation_threads: dict[str, str] | None = None,
        correlation_thread_store: Any = None,
        compatibility_request_topic: str = "agent.request.agent",
        default_request_message_type: str = DEFAULT_AGENT_REQUEST_MESSAGE_TYPE,
        default_response_message_type: str = DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE,
    ) -> None:
        self._publisher = publisher
        self._registry = registry
        self._correlation_threads = (
            correlation_threads if correlation_threads is not None else {}
        )
        self._correlation_thread_store = (
            correlation_thread_store
            or CorrelationThreadStore(fallback_threads=self._correlation_threads)
        )
        self._compatibility_request_topic = compatibility_request_topic
        self._default_request_message_type = (
            _non_empty_string(default_request_message_type)
            or DEFAULT_AGENT_REQUEST_MESSAGE_TYPE
        )
        self._default_response_message_type = (
            _non_empty_string(default_response_message_type)
            or DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE
        )

    async def delegate(self, request: AgentCallRequest) -> dict[str, Any]:
        queue_metadata = _coerce_mapping(getattr(request, "queue_metadata", None))
        if not queue_metadata:
            queue_metadata = await self._resolve_queue_metadata(request.agent_type)

        request_topic = (
            _non_empty_string(getattr(request, "request_topic", None))
            or _non_empty_string(queue_metadata.get("request_topic"))
            or _non_empty_string(queue_metadata.get("queue_name"))
            or self._compatibility_request_topic
        )
        request_message_type = self._resolve_request_message_type(
            request=request,
            queue_metadata=queue_metadata,
        )
        response_message_type = self._resolve_response_message_type(
            request=request,
            queue_metadata=queue_metadata,
        )
        reply_topic = request.reply_topic or queue_metadata.get("reply_topic")
        correlation_id = (
            request.correlation_id or request.thread_id or request.interrupt_id or ""
        )

        if correlation_id and request.thread_id:
            self._correlation_thread_store.remember(correlation_id, request.thread_id)

        message = {
            "type": request_message_type,
            "agent_id": request.agent_id,
            "agent_type": request.agent_type,
            "correlation_id": correlation_id,
            "thread_id": request.thread_id,
            "session_id": request.session_id,
            "interrupt_id": request.interrupt_id,
            "reply_topic": reply_topic,
            "reply_queue": request.reply_queue,
            "response_message_type": response_message_type,
            "metadata": dict(request.metadata or {}),
            "context_snapshot": dict(request.context_snapshot or {}),
            "input": dict(request.input_payload or {}),
        }

        if request.thread_id:
            message["conv_id"] = request.thread_id
            message["delegation"] = {"parent_thread_id": request.thread_id}

        if request.message_overrides:
            message.update(dict(request.message_overrides))
            message["type"] = request_message_type
            message["response_message_type"] = response_message_type

        await self._publisher.publish(
            topic=request_topic,
            message=message,
            key=correlation_id or None,
        )
        return {
            "type": "AGENT_CALL",
            "agent_id": request.agent_id,
            "agent_type": request.agent_type,
            "correlation_id": correlation_id,
            "thread_id": request.thread_id,
            "interrupt_id": request.interrupt_id,
            "reply_topic": reply_topic,
            "reply_queue": request.reply_queue,
            "request_topic": request_topic,
            "request_message_type": request_message_type,
            "response_message_type": response_message_type,
        }

    def _resolve_request_message_type(
        self,
        *,
        request: AgentCallRequest,
        queue_metadata: dict[str, Any],
    ) -> str:
        delivery_hints = queue_metadata.get("delivery_hints")
        if not isinstance(delivery_hints, dict):
            delivery_hints = {}

        return (
            _non_empty_string(request.request_message_type)
            or _non_empty_string(queue_metadata.get("request_message_type"))
            or _non_empty_string(queue_metadata.get("request_type"))
            or _non_empty_string(queue_metadata.get("message_type"))
            or _non_empty_string(delivery_hints.get("request_message_type"))
            or _non_empty_string(delivery_hints.get("request_type"))
            or _non_empty_string(delivery_hints.get("message_type"))
            or self._default_request_message_type
        )

    def _resolve_response_message_type(
        self,
        *,
        request: AgentCallRequest,
        queue_metadata: dict[str, Any],
    ) -> str:
        delivery_hints = queue_metadata.get("delivery_hints")
        if not isinstance(delivery_hints, dict):
            delivery_hints = {}

        return (
            _non_empty_string(request.response_message_type)
            or _non_empty_string(queue_metadata.get("response_message_type"))
            or _non_empty_string(queue_metadata.get("response_type"))
            or _non_empty_string(delivery_hints.get("response_message_type"))
            or _non_empty_string(delivery_hints.get("response_type"))
            or self._default_response_message_type
        )

    async def _resolve_queue_metadata(self, agent_type: str) -> dict[str, Any]:
        try:
            capabilities = await self._registry.list_capabilities()
        except RegistrationError:
            return {}

        for capability in capabilities or []:
            capability_data = _coerce_mapping(capability)
            if capability_data.get("agent_type") != agent_type:
                continue
            return _coerce_mapping(capability_data.get("queue_metadata"))

        return {}
