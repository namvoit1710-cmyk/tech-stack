from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from agent_sdk.layer1_domain.entities.message_handling_result import (
    MessageHandlingResult,
)
from agent_sdk.layer1_domain.value_objects.message_type_set import MessageTypeSet
from agent_sdk.layer1_domain.value_objects.transport_source import TransportSource
from agent_sdk.layer2_application.services.correlation_thread_store import (
    CorrelationThreadStore,
)
from agent_sdk.layer2_application.services.message_reaction.default_handlers import (
    DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE,
    EXECUTE_MESSAGE_TYPES,
    build_execute_input,
    build_resume_input,
)
from agent_sdk.layer2_application.services.message_type_settings import (
    configured_response_message_types,
)

CustomMessageHandler = Callable[..., Awaitable[Any] | Any]


class MessageReactionRouter:
    def __init__(
        self,
        *,
        execute_use_case: Any = None,
        resume_use_case: Any = None,
        custom_handlers: dict[str, CustomMessageHandler] | None = None,
        handler_dependencies: Mapping[str, Any] | None = None,
        correlation_threads: dict[str, str] | None = None,
        correlation_thread_store: Any = None,
        logger: Any = None,
    ) -> None:
        self._execute_use_case = execute_use_case
        self._resume_use_case = resume_use_case
        self._custom_handlers = custom_handlers or {}
        self._handler_dependencies = dict(handler_dependencies or {})
        self._correlation_threads = (
            correlation_threads if correlation_threads is not None else {}
        )
        self._correlation_thread_store = (
            correlation_thread_store
            or CorrelationThreadStore(fallback_threads=self._correlation_threads)
        )
        self._logger = logger
        self._response_message_types = self._configured_response_message_types()

    def register_custom_handler(
        self,
        message_type: str,
        handler: CustomMessageHandler,
    ) -> None:
        normalized_message_type = str(message_type or "").strip()
        if not normalized_message_type:
            raise ValueError("message_type must be a non-empty string")
        self._custom_handlers[normalized_message_type] = handler

    def register_custom_handlers(
        self,
        handlers: Mapping[str, CustomMessageHandler] | None,
    ) -> None:
        """Register multiple custom message handlers at runtime."""
        for message_type, handler in dict(handlers or {}).items():
            self.register_custom_handler(message_type, handler)

    @staticmethod
    def _coerce_message_type_set(value: Any) -> set[str]:
        return MessageTypeSet.coerce(value)

    def _configured_response_message_types(self) -> set[str]:
        return configured_response_message_types(
            self._handler_dependencies.get("settings"),
            default=DEFAULT_AGENT_RESPONSE_MESSAGE_TYPE,
        )

    def _is_response_message_type(self, message_type: str) -> bool:
        return message_type in self._response_message_types

    @staticmethod
    def _resolve_parent_thread_id(payload: dict[str, Any]) -> str:
        delegation = payload.get("delegation") or {}
        if not isinstance(delegation, dict):
            delegation = {}
        return (
            payload.get("thread_id")
            or delegation.get("parent_thread_id")
            or payload.get("parent_thread_id")
            or ""
        )

    async def _invoke_custom_handler(
        self,
        handler: CustomMessageHandler,
        payload: dict[str, Any],
        delivery: Any,
    ) -> MessageHandlingResult:
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError):
            result = handler(payload, self._handler_dependencies, delivery)
        else:
            params = signature.parameters
            accepts_varargs = any(
                param.kind == inspect.Parameter.VAR_POSITIONAL
                for param in params.values()
            )
            accepts_varkw = any(
                param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()
            )
            positional_params = [
                param
                for param in params.values()
                if param.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]

            if accepts_varkw:
                result = handler(
                    payload,
                    deps=self._handler_dependencies,
                    delivery=delivery,
                )
            elif accepts_varargs or len(positional_params) >= 3:
                result = handler(payload, self._handler_dependencies, delivery)
            elif "delivery" in params and "deps" in params:
                result = handler(
                    payload,
                    deps=self._handler_dependencies,
                    delivery=delivery,
                )
            elif "delivery" in params:
                result = handler(payload, delivery=delivery)
            elif len(positional_params) >= 2:
                result = handler(payload, self._handler_dependencies)
            elif "deps" in params:
                result = handler(payload, deps=self._handler_dependencies)
            else:
                result = handler(payload)

        if inspect.isawaitable(result):
            result = await result
        return MessageHandlingResult.from_value(result)

    async def _apply_custom_handler_result(
        self,
        delivery: Any,
        result: MessageHandlingResult,
    ) -> None:
        action = result.action.lower().strip()
        if action == "ack":
            await delivery.ack()
            return
        if action == "nack":
            await delivery.nack(requeue=result.requeue)
            return
        if action == "reject":
            await delivery.reject()
            return
        if action == "none":
            debug = getattr(self._logger, "debug", None)
            if callable(debug):
                debug(
                    "Handler returned 'none'; delivery left unacknowledged",
                    reason=result.reason,
                )
            return

        if self._logger:
            self._logger.warning(
                "Unsupported custom message handler action; rejecting delivery",
                action=action,
                reason=result.reason,
            )
        await delivery.reject()

    @staticmethod
    def _infer_delivery_source(delivery: Any) -> str:
        for attr_name in (
            "source",
            "transport_source",
            "messaging_source",
            "backend_name",
            "messaging_backend",
            "transport",
            "_source",
            "_backend_name",
            "_messaging_backend",
            "_transport",
        ):
            value = getattr(delivery, attr_name, None)
            if isinstance(value, str) and value.strip():
                return MessageReactionRouter._normalize_source(value)

        module_name = type(delivery).__module__.lower().replace("-", "_")
        class_name = type(delivery).__name__.lower().replace("-", "_")
        combined = f"{module_name}.{class_name}"

        if "event_mesh" in combined or "eventmesh" in combined:
            return "event_mesh"
        if "kafka" in combined:
            return "kafka"
        if "mock" in combined or "memory" in combined:
            return "mock"
        return "broker"

    @staticmethod
    def _normalize_source(value: str) -> str:
        return TransportSource.normalize(value, default=TransportSource.BROKER)

    def _annotate_transport_source(
        self,
        payload: dict[str, Any],
        delivery: Any,
    ) -> dict[str, Any]:
        if payload.get("source"):
            return payload

        source = self._infer_delivery_source(delivery)
        if not source:
            return payload

        annotated = dict(payload)
        annotated.setdefault("_sdk_transport_source", source)
        return annotated

    async def handle(self, delivery: Any) -> None:
        payload = getattr(delivery, "payload", delivery)
        if not isinstance(payload, dict):
            await delivery.reject()
            return

        try:
            message_type = payload.get("type") or ""

            if message_type in self._custom_handlers:
                result = await self._invoke_custom_handler(
                    self._custom_handlers[message_type],
                    payload,
                    delivery,
                )
                await self._apply_custom_handler_result(delivery, result)
                return

            if self._is_response_message_type(message_type):
                await self._handle_resume(delivery, payload)
                return

            if message_type and message_type not in EXECUTE_MESSAGE_TYPES:
                await delivery.reject()
                return

            await self._handle_execute(delivery, payload)

        except Exception:
            await delivery.nack(requeue=True)

    async def _handle_execute(self, delivery: Any, payload: dict[str, Any]) -> None:
        if self._execute_use_case is None:
            await delivery.reject()
            return

        try:
            execute_payload = self._annotate_transport_source(payload, delivery)
            request = build_execute_input(execute_payload)
        except (TypeError, ValueError):
            await delivery.reject()
            return

        await self._execute_use_case.execute(request)
        await delivery.ack()

    async def _handle_resume(self, delivery: Any, payload: dict[str, Any]) -> None:
        correlation_id = payload.get("correlation_id") or ""
        thread_id = self._resolve_parent_thread_id(
            payload
        ) or self._correlation_thread_store.resolve(correlation_id)

        if not thread_id:
            await delivery.nack(requeue=True)
            return

        if self._resume_use_case is None:
            await delivery.reject()
            return

        try:
            request = build_resume_input(payload, thread_id=thread_id)
        except (TypeError, ValueError):
            await delivery.reject()
            return

        await self._resume_use_case.execute(request)

        if correlation_id:
            self._correlation_thread_store.forget(correlation_id)

        await delivery.ack()
