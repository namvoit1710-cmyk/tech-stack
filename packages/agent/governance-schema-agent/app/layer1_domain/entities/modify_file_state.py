from __future__ import annotations

from typing import Any, Dict, TypedDict


class ModifyFileState(TypedDict, total=False):
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