from __future__ import annotations

import dataclasses
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator, Mapping

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata
from agent_sdk.layer1_domain.entities.workflow_event import WorkflowEvent

_logger = logging.getLogger(__name__)

_active_scope: ContextVar[dict | None] = ContextVar("_active_scope", default=None)


def _get_active_scope() -> dict | None:
    return _active_scope.get()


def _coerce_metadata(value: Any) -> ConversationMetadata:
    if isinstance(value, ConversationMetadata):
        return value
    if isinstance(value, dict):
        return ConversationMetadata(**value)
    return ConversationMetadata()


def _serialize_metadata(value: ConversationMetadata) -> dict[str, Any]:
    return dataclasses.asdict(value)


def _derive_scope_request_context(request: Any | None) -> dict[str, Any]:
    if request is None:
        return {
            "conv_id": "",
            "correlation_id": None,
            "reply_to": None,
            "metadata": _serialize_metadata(ConversationMetadata()),
            "uploaded_file_ids": [],
        }

    metadata = _coerce_metadata(getattr(request, "metadata", None))
    uploaded_file_ids = list(
        getattr(request, "uploaded_file_ids", None) or metadata.uploaded_file_ids or []
    )
    conv_id = getattr(request, "conv_id", None) or getattr(request, "thread_id", "")
    reply_to = getattr(request, "reply_to", None) or getattr(
        request, "reply_topic", None
    )
    correlation_id = getattr(request, "correlation_id", None)

    return {
        "conv_id": conv_id,
        "correlation_id": correlation_id,
        "reply_to": reply_to,
        "metadata": _serialize_metadata(metadata),
        "uploaded_file_ids": uploaded_file_ids,
    }


@contextmanager
def workflow_event_scope(
    emitter: Any,
    *,
    request: Any | None = None,
    mode: str = "execute",
) -> Generator[dict, None, None]:
    derived_context = _derive_scope_request_context(request)
    scope = {
        "emitter": emitter,
        "request": request,
        "mode": mode,
        **derived_context,
    }
    token = _active_scope.set(scope)
    try:
        yield scope
    finally:
        _active_scope.reset(token)


async def emit_workflow_event(
    event: WorkflowEvent,
    *,
    state: Mapping[str, Any] | None = None,
    node_id: str | None = None,
    topic: str | None = None,
) -> None:
    scope = _get_active_scope()
    if scope is None:
        _logger.debug(
            "emit_workflow_event called with no active workflow_event_scope; event dropped."
        )
        return

    emitter = scope.get("emitter")
    if emitter is None:
        return

    await emitter.emit(event, state=state, node_id=node_id, topic=topic)
