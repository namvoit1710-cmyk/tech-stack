from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import Any


def _filter_known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys that map to dataclass fields.

    The agent-registry response may include extra keys (e.g. capability_summary)
    that this client does not model; cls(**data) would raise TypeError on those.
    """
    known = {f.name for f in fields(cls)}
    return {k: v for k, v in (data or {}).items() if k in known}


@dataclass
class AgentHub:
    id: str
    name: str
    kind: str
    status: str
    is_published: bool

    description: str
    healthcheck_endpoint: str
    invoke_endpoint: str

    is_alive: bool
    last_health_check_at: datetime | None

    version: str
    config_type: str
    created_at: datetime
    updated_at: datetime
    capabilities: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    workflows: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    knowledge_base: list[str] = field(default_factory=list)

    metadata: dict[str, Any] = field(default_factory=dict)

    business: str = ""
    user_email: str = ""
    tenant_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentHub":
        return cls(**_filter_known(cls, data))


@dataclass
class AgentHubDTO:
    id: str
    name: str
    description: str
    capabilities: list[str]
    business: str
    metadata: dict[str, Any]

    @staticmethod
    def to_dto(agent: "AgentHub") -> "AgentHubDTO":
        return AgentHubDTO(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            capabilities=agent.capabilities,
            business=agent.business,
            metadata=agent.metadata,
        )


@dataclass
class ToolHub:
    id: str
    name: str
    description: str
    protocol: str
    endpoint: str
    version: str
    status: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    auth_config: dict[str, Any] = field(default_factory=dict)
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolHub":
        return cls(**_filter_known(cls, data))


@dataclass
class ToolHubDTO:
    id: str
    name: str
    description: str
    input_data: dict[str, Any]
    output_data: dict[str, Any]

    @staticmethod
    def to_dto(tool: "ToolHub") -> "ToolHubDTO":
        return ToolHubDTO(
            id=tool.id,
            name=tool.name,
            description=tool.description,
            input_data=tool.input_data,
            output_data=tool.output_data,
        )


@dataclass
class ToolHubResponse:
    tools: list[ToolHub] = field(default_factory=list)
    total: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolHubResponse":
        return cls(
            tools=[ToolHub.from_dict(t) for t in data.get("tools", [])],
            total=data.get("total", 0),
        )


@dataclass
class WorkflowHub:
    id: str
    name: str
    description: str
    version: str
    status: str
    main_flow: bool
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowHub":
        return cls(**_filter_known(cls, data))


@dataclass
class WorkflowHubDTO:
    id: str
    name: str
    description: str

    @staticmethod
    def to_dto(workflow: "WorkflowHub") -> "WorkflowHubDTO":
        return WorkflowHubDTO(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
        )


@dataclass
class WorkflowHubResponse:
    workflows: list[WorkflowHub] = field(default_factory=list)
    total: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowHubResponse":
        return cls(
            workflows=[WorkflowHub.from_dict(w) for w in data.get("workflows", [])],
            total=data.get("total", 0),
        )
