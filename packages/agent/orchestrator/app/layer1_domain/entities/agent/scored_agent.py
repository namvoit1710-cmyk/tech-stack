from dataclasses import dataclass
from enum import Enum


class AgentKind(str, Enum):
    TECHNICAL = "technical"
    BUSINESS = "business"

class AgentStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    DEPRECATED = "DEPRECATED"
@dataclass
class ScoredAgent:
    """Registry-facing representation of an agent returned by filter API."""
    id: str
    name: str
    kind: AgentKind
    status: AgentStatus
    is_published: bool
    description: str
    healthcheck_endpoint: str
    invoke_endpoint: str
    is_alive: bool
    last_heartbeat: str | None
    version: str
    config_type: str
    capabilities: list[str]
    agents: list[str]
    workflows: list[str]
    tools: list[str]
    metadata: dict
    business: str
    created_at: str | None
    updated_at: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "is_published": self.is_published,
            "description": self.description,
            "healthcheck_endpoint": self.healthcheck_endpoint,
            "invoke_endpoint": self.invoke_endpoint,
            "is_alive": self.is_alive,
            "last_heartbeat": self.last_heartbeat,
            "version": self.version,
            "config_type": self.config_type,
            "capabilities": self.capabilities,
            "agents": self.agents,
            "workflows": self.workflows,
            "tools": self.tools,
            "metadata": self.metadata,
            "business": self.business,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

