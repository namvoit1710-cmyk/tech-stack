"""Update Agent DTO - Input contract for updating an existing agent."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateAgentDTO:
    """Input DTO for updating an existing agent.
    
    All fields are optional. Only provided fields will be updated.
    Note: status is NOT updatable via this DTO.
    Use dedicated use cases for activation/deactivation.
    """

    is_published: bool | None = None  # Whether agent is published
    name: str | None = None
    kind: str | None = None  # "business" or "technical"
    description: str | None = None
    config_type: str | None = None  # "default" or "custom"
    business: str | None = None  # Business context / system prompt
    tools: list[str] | None = None  # Tool IDs to attach
    workflows: list[str] | None = None  # Workflow IDs to attach
    agents: list[str] | None = None  # Agent IDs to attach
    knowledge_base: list[str] | None = None  # Document IDs to attach
    version: str | None = None
    healthcheck_endpoint: str | None = None
    invoke_endpoint: str | None = None
    metadata: dict | None = None
    model: str | None = None  # LLM model (e.g. gpt-4)
    provider: str | None = None  # LLM provider (e.g. openai)
    temperature: float | None = None  # LLM temperature setting
    custom_system_prompt: str | None = None  # Custom system prompt overriding the default (optional)
    custom_instructions: list[str] | None = None  # Custom instructions for the agent (optional)
    custom_restrictions: list[str] | None = None  # Custom restrictions for the agent (optional)
    blocked_topics: list[str] | None = None  # Topics the agent should avoid (optional)
    blocked_keywords: list[str] | None = None  # Keywords the agent should avoid (optional)

    @classmethod
    def from_api_request(cls, request: dict) -> "UpdateAgentDTO":
        """Create DTO from API request data.
        
        Args:
            request: Dictionary with request data
            
        Returns:
            UpdateAgentDTO instance
        """
        return cls(
            name=request.get("name"),
            kind=request.get("kind"),
            description=request.get("description"),
            is_published=request.get("is_published"),
            config_type=request.get("config_type"),
            business=request.get("business"),
            tools=request.get("tools"),
            workflows=request.get("workflows"),
            agents=request.get("agents"),
            knowledge_base=request.get("knowledge_base"),
            version=request.get("version"),
            healthcheck_endpoint=request.get("healthcheck_endpoint"),
            invoke_endpoint=request.get("invoke_endpoint"),
            metadata=request.get("metadata"),
            model=request.get("model"),
            provider=request.get("provider"),
            temperature=request.get("temperature"),
            custom_system_prompt=request.get("custom_system_prompt"),
            custom_instructions=request.get("custom_instructions"),
            custom_restrictions=request.get("custom_restrictions"),
            blocked_topics=request.get("blocked_topics"),
            blocked_keywords=request.get("blocked_keywords"),
        )
