from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from agent_sdk.layer1_domain.entities.ui_events import (
    BaseUiEvent,
    ChatDisabledEvent,
    ChatEnabledEvent,
)
from agent_sdk.layer1_domain.entities.workflow_event import (
    EVENT_WORKFLOW_COMPLETED,
    EVENT_WORKFLOW_FAILED,
)
from agent_sdk.layer2_application.interfaces.message_publisher import IMessagePublisher
from agent_sdk.layer2_application.interfaces.observability import ILogger
from agent_sdk.layer2_application.interfaces.push_gateway_notifier import (
    PushGatewayNotifierProtocol,
)

_AUTO_CORRELATION_ID = "auto-generated"

_TERMINAL_EVENT_TYPES = {EVENT_WORKFLOW_COMPLETED, EVENT_WORKFLOW_FAILED}


def _coerce_payload_dict(value: Any) -> dict[str, Any]:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise TypeError(f"Unsupported event payload type: {type(value)!r}")


def _is_ui_event_name(event_name: str) -> bool:
    return event_name.startswith("chat:")


def _serialize_ui_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)

    if dataclasses.is_dataclass(value):
        raw_payload = dataclasses.asdict(value)
    elif hasattr(value, "__dict__"):
        raw_payload = dict(vars(value))
    else:
        raise TypeError(f"Unsupported UI payload type: {type(value)!r}")

    payload_type = str(raw_payload.get("type") or "")
    canonical_payload: dict[str, Any] = {}

    common_fields = ("id", "parent_id", "status", "wf_info")
    for field_name in common_fields:
        field_value = raw_payload.get(field_name)
        if field_value not in (None, "", []):
            canonical_payload[field_name] = field_value

    if payload_type:
        canonical_payload["type"] = payload_type

    if payload_type == "text":
        canonical_payload["content"] = raw_payload.get("content") or raw_payload.get(
            "text", ""
        )
        return canonical_payload

    if payload_type == "progressing_collapse":
        canonical_payload["content"] = (
            raw_payload.get("content")
            or raw_payload.get("message")
            or raw_payload.get("title", "")
        )
        return canonical_payload

    if payload_type == "button_group":
        text_values = raw_payload.get("text") or []
        if text_values:
            canonical_payload["text"] = list(text_values)
        buttons = raw_payload.get("buttons") or []
        if buttons and "text" not in canonical_payload:
            canonical_payload["text"] = [button.get("label", "") for button in buttons]
        canonical_payload["content"] = raw_payload.get("content") or raw_payload.get(
            "title", ""
        )
        return canonical_payload

    if payload_type == "open_workspace":
        canonical_payload["content"] = raw_payload.get("content", "")
        return canonical_payload

    if payload_type == "tool_form":
        canonical_payload["content"] = raw_payload.get("content") or raw_payload.get(
            "tool_name", ""
        )
        return canonical_payload

    if payload_type == "summary":
        canonical_payload["content"] = raw_payload.get("content") or raw_payload.get(
            "text", ""
        )
        return canonical_payload

    return canonical_payload or raw_payload


def _normalize_timestamp(value: str | float | int | None) -> str:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def _serialize_ui_event(event: Any) -> dict[str, Any]:
    event_type = str(
        getattr(event, "event_type", "") or getattr(event, "type", "") or ""
    )
    payload: dict[str, Any] = {
        "event_id": str(getattr(event, "event_id", "") or uuid4()),
        "event_type": event_type,
        "correlation_id": getattr(event, "correlation_id", "") or "",
        "timestamp": _normalize_timestamp(getattr(event, "timestamp", None)),
        "conv_id": getattr(event, "conv_id", "")
        or getattr(event, "conversation_id", "")
        or "",
        "metadata": _coerce_payload_dict(getattr(event, "metadata", {}) or {}),
    }

    event_payload = getattr(event, "payload", {})
    payload["payload"] = _serialize_ui_payload(event_payload or {})

    return payload


def serialize_event(event: Any) -> dict:
    if isinstance(event, BaseUiEvent) or _is_ui_event_name(
        str(getattr(event, "event_type", "") or getattr(event, "type", "") or "")
    ):
        return _serialize_ui_event(event)
    return _coerce_payload_dict(event)


def _merge_ui_metadata(
    payload: dict[str, Any],
    metadata_value: Any,
    uploaded_file_ids_value: Any,
) -> None:
    metadata = _coerce_payload_dict(metadata_value or {})

    if uploaded_file_ids_value and not metadata.get("uploaded_file_ids"):
        metadata["uploaded_file_ids"] = list(uploaded_file_ids_value)

    if metadata and not payload.get("metadata"):
        payload["metadata"] = metadata


class WorkflowEventEmitter:
    def __init__(
        self,
        publisher: IMessagePublisher,
        logger: ILogger | None = None,
        push_gateway_notifier: PushGatewayNotifierProtocol | None = None,
        raise_on_error: bool = False,
    ) -> None:
        self._publisher = publisher
        self._logger = logger
        self._push_gateway_notifier = push_gateway_notifier
        self._raise_on_error = raise_on_error

    def _is_final_event(self, event_type: str) -> bool:
        return event_type in _TERMINAL_EVENT_TYPES

    def _apply_scope_defaults(
        self,
        payload: dict[str, Any],
        *,
        scope: Mapping[str, Any] | None,
        state: Mapping[str, Any] | None,
        node_id: str | None,
    ) -> dict[str, Any]:
        is_ui_event = _is_ui_event_name(
            str(payload.get("event_type") or payload.get("type") or "")
        )

        if scope is not None:
            scope_conv_id = scope.get("conv_id")
            if payload.get("conv_id") in (None, "") and scope_conv_id:
                payload["conv_id"] = scope_conv_id
            if (
                not is_ui_event
                and payload.get("conversation_id") == ""
                and scope_conv_id
            ):
                payload["conversation_id"] = scope_conv_id

            scope_correlation_id = scope.get("correlation_id")
            if (
                payload.get("correlation_id") in (None, "", _AUTO_CORRELATION_ID)
                and scope_correlation_id
            ):
                payload["correlation_id"] = scope_correlation_id

            if is_ui_event:
                _merge_ui_metadata(
                    payload,
                    scope.get("metadata"),
                    scope.get("uploaded_file_ids"),
                )
            else:
                if not payload.get("metadata") and scope.get("metadata"):
                    payload["metadata"] = dict(scope["metadata"])
                if not payload.get("uploaded_file_ids") and scope.get(
                    "uploaded_file_ids"
                ):
                    payload["uploaded_file_ids"] = list(scope["uploaded_file_ids"])

        if not payload.get("node_id") and node_id is not None:
            payload["node_id"] = node_id

        if state is not None:
            if not payload.get("conv_id") and state.get("conv_id"):
                payload["conv_id"] = state["conv_id"]
            if (
                not is_ui_event
                and not payload.get("conversation_id")
                and state.get("conv_id")
            ):
                payload["conversation_id"] = state["conv_id"]
            if payload.get("correlation_id") in (
                None,
                "",
                _AUTO_CORRELATION_ID,
            ) and state.get("correlation_id"):
                payload["correlation_id"] = state["correlation_id"]
            if not payload.get("workflow_id") and state.get("workflow_id"):
                payload["workflow_id"] = state["workflow_id"]
            if not payload.get("node_id") and state.get("node_id"):
                payload["node_id"] = state["node_id"]
            if is_ui_event:
                _merge_ui_metadata(
                    payload,
                    state.get("metadata"),
                    state.get("uploaded_file_ids"),
                )
            else:
                if not payload.get("metadata") and state.get("metadata"):
                    payload["metadata"] = _coerce_payload_dict(state["metadata"])
                if not payload.get("uploaded_file_ids") and state.get(
                    "uploaded_file_ids"
                ):
                    payload["uploaded_file_ids"] = list(state["uploaded_file_ids"])

        return payload

    def _resolve_topic(
        self,
        payload: Mapping[str, Any],
        scope: Mapping[str, Any] | None,
        topic: str | None,
    ) -> str:
        if topic is not None:
            return topic
        if scope is not None and scope.get("reply_to"):
            return f"{scope['reply_to']}.progress"
        return str(payload.get("event_type") or payload.get("type") or "event")

    async def emit_ui_event(
        self,
        event: Any,
        *,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None:
        await self.emit(event, state=state, node_id=node_id, topic=topic)

    async def emit_chat_enabled(
        self,
        *,
        conversation_id: str = "",
        payload: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None:
        await self.emit_ui_event(
            ChatEnabledEvent(conv_id=conversation_id, payload=dict(payload or {})),
            state=state,
            node_id=node_id,
            topic=topic,
        )

    async def emit_chat_disabled(
        self,
        *,
        conversation_id: str = "",
        payload: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None:
        await self.emit_ui_event(
            ChatDisabledEvent(conv_id=conversation_id, payload=dict(payload or {})),
            state=state,
            node_id=node_id,
            topic=topic,
        )

    async def emit_orchestration_event(
        self,
        event: Any,
        *,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None:
        await self.emit(event, state=state, node_id=node_id, topic=topic)

    async def emit(
        self,
        event: Any,
        *,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None:
        from agent_sdk.layer2_application.services.workflow_event_runtime import (
            _get_active_scope,
        )

        scope = _get_active_scope()

        payload = serialize_event(event)

        ts = payload.get("timestamp")
        if isinstance(ts, (int, float)):
            payload["timestamp"] = datetime.fromtimestamp(
                ts, tz=timezone.utc
            ).isoformat()
        elif ts is None:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()

        payload = self._apply_scope_defaults(
            payload,
            scope=scope,
            state=state,
            node_id=node_id,
        )

        if not payload.get("correlation_id"):
            payload["correlation_id"] = str(uuid4())

        resolved_topic = self._resolve_topic(payload, scope, topic)

        correlation_id = payload.get("correlation_id", "")
        key = (
            correlation_id
            if correlation_id and correlation_id != _AUTO_CORRELATION_ID
            else ""
        )

        try:
            await self._publisher.publish(resolved_topic, payload, key=key)
        except Exception as exc:
            if self._logger is not None:
                self._logger.error(
                    "WorkflowEventEmitter: publish failed for topic=%s error=%s",
                    resolved_topic=resolved_topic,
                    error=str(exc),
                )
            if self._raise_on_error:
                raise

        conv_id = payload.get("conv_id", "")
        if not conv_id:
            conv_id = payload.get("conversation_id", "")
        if self._push_gateway_notifier is not None and conv_id:
            try:
                is_final = self._is_final_event(
                    str(payload.get("event_type") or payload.get("type") or "")
                )
                await self._push_gateway_notifier.send_notification(
                    key=conv_id,
                    data=payload,
                    is_final=is_final,
                )
            except Exception as exc:
                if self._logger is not None:
                    self._logger.error(
                        "WorkflowEventEmitter: push gateway notification failed for key=%s error=%s",
                        key=conv_id,
                        error=str(exc),
                    )
                if self._raise_on_error:
                    raise
