from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentCallRequest:
    agent_id: str
    agent_type: str
    input_payload: dict
    interrupt_id: Optional[str]
    thread_id: Optional[str]
    correlation_id: Optional[str] = None
    session_id: Optional[str] = None
    reply_topic: Optional[str] = None
    reply_queue: Optional[str] = None
    request_topic: Optional[str] = None
    queue_metadata: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    context_snapshot: dict[str, Any] = field(default_factory=dict)
    request_message_type: Optional[str] = None
    response_message_type: Optional[str] = None
    message_overrides: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentCallResult:
    success: bool
    response: Any
    agent_id: str
    error: Optional[str] = None
