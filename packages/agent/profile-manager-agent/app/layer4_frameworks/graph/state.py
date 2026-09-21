from typing import Any, Dict, TypedDict


class OdataQueryState(TypedDict, total=False):
    """LangGraph state schema for the profile-manager graph.

    A framework (LangGraph ``TypedDict``) detail, so it lives in layer4 — not in
    the domain where it used to sit (P5).
    """

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
