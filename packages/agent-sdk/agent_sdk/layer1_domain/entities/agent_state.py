from typing import Any, Dict, Optional, TypedDict

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata


class AgentBaseState(TypedDict, total=False):
    message: str
    conv_id: str
    session_id: str
    user_id: str
    tenant_id: str
    source: str
    correlation_id: Optional[str]
    trace_id: Optional[str]
    transport_state: str
    parameters: dict
    metadata: ConversationMetadata | Dict[str, Any]
    uploaded_file_ids: list[str]
    execution_context: Dict[str, Any]
    context_snapshot: Dict[str, Any]
    shared_state: Dict[str, Any]
    shared_state_key: str
    shared_state_version: int
    error: Optional[str]
    error_code: Optional[str]
    formatted_response: Optional[dict]
    input_guard_result: Optional[dict]
    output_guard_result: Optional[dict]
    permission_context: Any
    tenant_context: Optional[Dict[str, Any]]
    context: Optional[Dict[str, Any]]
