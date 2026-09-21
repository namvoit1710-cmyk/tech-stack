"""Audit log entry entity (ADR-009 permission flow).

Records permission-related events for compliance and debugging.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class AuditAction(str, Enum):
    PERMISSION_COLLECTED = "permission_collected"
    PERMISSION_DENIED = "permission_denied"
    AGENT_ACCESS_GRANTED = "agent_access_granted"
    AGENT_ACCESS_DENIED = "agent_access_denied"
    WORKFLOW_ACCESS_GRANTED = "workflow_access_granted"
    WORKFLOW_ACCESS_DENIED = "workflow_access_denied"
    INPUT_REJECTED = "input_rejected"
    OUTPUT_REJECTED = "output_rejected"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"


@dataclass
class AuditLogEntry:
    """A single audit log record."""

    entry_id: str
    user_id: str
    tenant_id: str
    action: AuditAction
    resource_type: str = ""  # "agent" | "workflow" | "conversation"
    resource_id: str = ""
    conversation_id: Optional[str] = None
    details: dict[str, Any] = field(default_factory=dict)
    outcome: str = "success"  # "success" | "denied" | "error"
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "action": self.action.value,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "conversation_id": self.conversation_id,
            "details": self.details,
            "outcome": self.outcome,
            "timestamp": self.timestamp,
        }
