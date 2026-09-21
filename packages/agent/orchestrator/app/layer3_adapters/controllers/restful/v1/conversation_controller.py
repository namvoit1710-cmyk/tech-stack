"""Conversations controller for the orchestrator service."""

from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

from app.layer2_application.features.conversation.use_cases.get_conversation_use_case import GetConversationInput
from app.layer2_application.features.conversation.use_cases.get_conversation_messages_use_case import GetConversationMessagesInput
from app.layer2_application.features.conversation.use_cases.list_conversations_use_case import ListConversationsInput
from .usecase_resolver import resolve_usecase


# --- DTOs ---
def _strip_id(id: str) -> str:
    return id.strip()


def _build_conversation_lookup_input(conversation_id: str) -> GetConversationInput:
    return GetConversationInput(conversation_id=_strip_id(conversation_id))


def _build_conversation_messages_input(conversation_id: str) -> GetConversationMessagesInput:
    return GetConversationMessagesInput(conversation_id=_strip_id(conversation_id))


def _build_list_conversations_input(
    user_id: str,
    tenant_id: str,
    page_index: int,
    page_size: int,
) -> ListConversationsInput:
    return ListConversationsInput(
        user_id=user_id,
        tenant_id=tenant_id,
        page_index=page_index,
        page_size=page_size,
    )

def _serialize_result(result):
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    model_dump = getattr(result, "model_dump", None)
    if callable(model_dump):
        return model_dump()
    return result


# --- Dependency Getters ---

def _usecase_dependency(usecase_name: str) -> Callable[[Request], object]:
    def dependency(request: Request):
        return resolve_usecase(request, usecase_name)

    return dependency


get_get_conversation_usecase = _usecase_dependency("conversation.get_conversation")
get_get_conversation_messages_usecase = _usecase_dependency("conversation.get_conversation_messages")
get_list_conversations_usecase = _usecase_dependency("conversation.list_conversations")
get_add_message_usecase = _usecase_dependency("conversation.add_message")


# --- Router ---

conversations_router = APIRouter(tags=["conversations"])


# --- Routes ---

@conversations_router.get("")
def list_conversations(
    user_id: str = "",
    tenant_id: str = "",
    page_index: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1),
    usecase=Depends(get_list_conversations_usecase),
):
    result = usecase.execute(_build_list_conversations_input(user_id, tenant_id, page_index, page_size))
    return _serialize_result(result)


@conversations_router.get("/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: str,
    usecase=Depends(get_get_conversation_messages_usecase),
):
    result = usecase.execute(_build_conversation_messages_input(conversation_id))
    return _serialize_result(result)


@conversations_router.get("/{conversation_id}")
def get_conversation(
    conversation_id: str,
    usecase=Depends(get_get_conversation_usecase),
):
    result = usecase.execute(_build_conversation_lookup_input(conversation_id))
    return _serialize_result(result)

