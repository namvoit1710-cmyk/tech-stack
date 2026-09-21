from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, TypedDict


@dataclass
class ConversationTurn:
    role: str
    message: str
    file_id: str | None = None


class FileState(TypedDict, total=False):
    message: str
    conversation_id: str
    user_id: str
    tenant_id: str
    source: str
    transport_state: str
    agent_result: Dict[str, Any]
    formatted_response: Dict[str, Any]
    error: str
    error_code: str
    parameters: Dict[str, Any]
    tool_catalog: list[dict[str, Any]]
    execution_plan: dict[str, Any]
    execution_results: list[dict[str, Any]]
    conversation_history: list[ConversationTurn]