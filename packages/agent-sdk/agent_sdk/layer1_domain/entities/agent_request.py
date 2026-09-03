from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_sdk.layer1_domain.entities.conversation_metadata import ConversationMetadata


@dataclass
class RequestContext:
    agent: str = ""
    source: str = "api"
    history: List[str] = field(default_factory=list)


@dataclass
class AgentRequest:
    message: str
    conv_id: str = ""
    session_id: str = ""
    user_id: str = "anonymous"
    tenant_id: str = "default"
    source: str = "api"
    correlation_id: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    context: Optional[RequestContext] = None
    metadata: ConversationMetadata = field(default_factory=ConversationMetadata)
    uploaded_file_ids: List[str] = field(default_factory=list)
    reply_to: Optional[str] = None
    reply_topic: Optional[str] = None
    reply_queue: Optional[str] = None
    action: Optional[str] = None
    agent_type: Optional[str] = None
    intent: Dict[str, Any] = field(default_factory=dict)
    execution_context: Dict[str, Any] = field(default_factory=dict)
    context_snapshot: Dict[str, Any] = field(default_factory=dict)
