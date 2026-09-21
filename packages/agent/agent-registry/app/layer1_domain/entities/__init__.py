"""Domain entities package."""

from app.layer1_domain.entities.agent import Agent, AgentKind, AgentStatus, ConfigType
from app.layer1_domain.entities.principal import KIND_SYSTEM, KIND_USER, Principal
from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.entities.workflow import Workflow

__all__ = [
    # Agent
    "Agent",
    "AgentKind",
    "AgentStatus",
    "ConfigType",
    # Principal (RBAC v1)
    "Principal",
    "KIND_USER",
    "KIND_SYSTEM",
    # Tool
    "Tool",
    "ToolProtocol",
    "ToolStatus",
    # Workflow
    "Workflow",
]
