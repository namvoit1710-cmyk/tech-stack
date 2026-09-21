from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest


@dataclass
class TenantContext:
    tenant_id: str
    user_id: str
    conv_id: str
    source: str = "api"
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_request(cls, request: "AgentRequest") -> "TenantContext":
        return cls(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            conv_id=request.conv_id,
            source=request.source,
            correlation_id=request.correlation_id,
        )
