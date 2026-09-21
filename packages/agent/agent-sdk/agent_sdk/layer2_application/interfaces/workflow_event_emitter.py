from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class IWorkflowEventEmitter(Protocol):
    async def emit(
        self,
        event: Any,
        *,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None: ...

    async def emit_ui_event(
        self,
        event: Any,
        *,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None: ...

    async def emit_chat_enabled(
        self,
        *,
        conversation_id: str = "",
        payload: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None: ...

    async def emit_chat_disabled(
        self,
        *,
        conversation_id: str = "",
        payload: Mapping[str, Any] | None = None,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None: ...

    async def emit_orchestration_event(
        self,
        event: Any,
        *,
        state: Mapping[str, Any] | None = None,
        node_id: str | None = None,
        topic: str | None = None,
    ) -> None: ...
