"""Base event schema for orchestrator and notification plumbing.

The format is:
{
  "event_id": "uuid",
  "event_type": "string",
  "correlation_id": "string",
  "timestamp": "ISO-8601",
  "conv_id": "string",
  "data": {},
  "error": null,
  "meta": {}
}
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

class EventType(str, Enum):
    CHAT_USER_MESSAGE = "chat:user:message"
    CHAT_THINKING = "chat:thinking"
    CHAT_AGENT_SELECTED = "chat:agent:selected"
    CHAT_AGENT_RESULT = "chat:agent:result"
    CHAT_RESPONSE = "chat:response"
    CHAT_RESPONSE_STREAM = "chat:response:stream"
    CHAT_ERROR = "chat:error"
    WORKFLOW_STATUS = "workflow:status"
    WORKFLOW_UI_RENDER = "workflow:ui:render"
    WORKFLOW_CHAT_DISABLE = "workflow:chat:disable"
    WORKFLOW_NODE_STATUS = "workflow:node:status"
    WORKFLOW_CHAT_ENABLE = "workflow:chat:enable"
    WORKFLOW_UI_REDIRECT = "workflow:ui:redirect"
    WORKFLOW_COMPLETED = "workflow:completed"
    WORKFLOW_INPUT_REQUESTED = "workflow:input.requested"
    WORKFLOW_DATA_EDIT_REQUESTED = "workflow:data_edit.requested"
    WORKFLOW_HUMAN_ACTION_REQUESTED = "workflow:human_action.requested"
    WORKFLOW_INPUT_SUBMITTED = "workflow:input.submitted"
    WORKFLOW_DATA_EDIT_COMPLETED = "workflow:data_edit.completed"
    WORKFLOW_HUMAN_ACTION_COMPLETED = "workflow:human_action.completed"


@dataclass
class BaseEventSchema:
    event_id: str
    event_type: EventType
    correlation_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    conv_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        event_type: EventType,
        correlation_id: str,
        conv_id: str,
        payload: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> "BaseEventSchema":
        return cls(
            event_id=event_id,
            event_type=event_type,
            correlation_id=correlation_id,
            conv_id=conv_id,
            payload=payload or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "conv_id": self.conv_id,
            "payload": self.payload,
            "metadata": self.metadata,
        }




