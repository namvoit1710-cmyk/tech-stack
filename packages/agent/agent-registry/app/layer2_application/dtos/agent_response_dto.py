"""Agent Response DTO - Output contract for agent data."""

from dataclasses import dataclass, field
from datetime import datetime

from app.layer1_domain.entities.agent import Agent


@dataclass(frozen=True)
class AgentResponseDTO:
    """Output DTO for agent data returned to API layer.
    
    This is the contract for what data flows out of the application layer.
    """

    id: str
    name: str
    kind: str
    status: str
    is_published: bool
    description: str
    healthcheck_endpoint: str | None
    invoke_endpoint: str | None
    is_alive: bool
    last_health_check_at: datetime | None
    config_type: str
    version: str
    provider: str | None  # LLM provider as supplied by the caller; None = not chosen
    model: str | None  # LLM model as supplied by the caller; None = not chosen
    temperature: float | None  # LLM temperature; None = caller did not choose one
    capabilities: list[str]
    capability_summary: str
    metadata: dict
    created_at: datetime
    updated_at: datetime
    tools: list[str]
    workflows: list[str]
    agents: list[str]
    knowledge_base: list[str]
    business: str
    user_email: str | None
    tenant_id: str | None
    custom_system_prompt: str | None = None  # Custom system prompt overriding the default (optional)
    custom_instructions: list[str] = field(default_factory=list)  # Custom instructions for the agent
    custom_restrictions: list[str] = field(default_factory=list)  # Custom restrictions for the agent
    blocked_topics: list[str] = field(default_factory=list)  # Topics the agent should avoid
    blocked_keywords: list[str] = field(default_factory=list)  # Keywords the agent should avoid
    user_roles: list[str] = field(default_factory=list)  # User user_roles for access control

    @classmethod
    def from_entity(cls, agent: Agent) -> "AgentResponseDTO":
        """Convert domain entity to DTO.
        
        Args:
            agent: Agent entity
            
        Returns:
            AgentResponseDTO instance
        """
        return cls(
            id=agent.id,
            name=agent.name,
            kind=agent.kind.value,
            status=agent.status.value,
            is_published=agent.is_published,
            description=agent.description,
            healthcheck_endpoint=agent.healthcheck_endpoint,
            invoke_endpoint=agent.invoke_endpoint,
            is_alive=agent.is_alive,
            last_health_check_at=agent.last_health_check_at,
            config_type=agent.config_type,
            version=agent.version,
            provider=agent.provider,
            model=agent.model,
            temperature=agent.temperature,
            capabilities=agent.capabilities,
            capability_summary=agent.capability_summary,
            tools=agent.tools,
            workflows=agent.workflows,
            agents=agent.agents,
            knowledge_base=agent.knowledge_base,
            metadata=agent.metadata,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
            business=agent.system_prompt,
            user_email=agent.user_email,
            tenant_id=agent.tenant_id,
            custom_system_prompt=agent.custom_system_prompt,
            custom_instructions=agent.custom_instructions,
            custom_restrictions=agent.custom_restrictions,
            blocked_topics=agent.blocked_topics,
            blocked_keywords=agent.blocked_keywords,
            user_roles=agent.user_roles,
        )

    @classmethod
    def from_entities(cls, agents: list[Agent]) -> list["AgentResponseDTO"]:
        """Convert list of domain entities to DTOs.
        
        Args:
            agents: List of Agent entities
            
        Returns:
            List of AgentResponseDTO instances
        """
        return [cls.from_entity(agent) for agent in agents]