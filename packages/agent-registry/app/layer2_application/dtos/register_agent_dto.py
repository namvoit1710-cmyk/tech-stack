"""Register Agent DTO - Input contract for registering a new agent."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RegisterAgentDTO:
    """Input DTO for registering a new agent.
    
    This is the contract between the API layer and the use case.
    """

    name: str
    kind: str  # "business" or "technical"
    status: str  # "active", "inactive"
    description: str
    is_published: bool = False
    config_type: str = "default"  # "default" or "custom"
    business: str = ""  # Business context / system prompt
    tools: list[str] | None = None  # Tool IDs to attach
    workflows: list[str] | None = None  # Workflow IDs to attach
    agents: list[str] | None = None  # Agent IDs to attach
    knowledge_base: list[str] | None = None  # Document IDs to attach
    version: str = "1.0.0"
    healthcheck_endpoint: str | None = None
    invoke_endpoint: str | None = None
    metadata: dict | None = None
    model: str | None = None  # LLM model (e.g. gpt-4)
    provider: str | None = None  # LLM provider (e.g. openai)
    temperature: float | None = None  # LLM temperature setting
    max_tokens: int | None = None
    timeout_ms: int | None = None
    max_concurrency: int | None = None
    retry_count: int | None = None
    streaming_supported: bool | None = None
    custom_system_prompt: str | None = None  # Custom system prompt overriding the default (optional)
    custom_instructions: list[str] | None = None  # Custom instructions for the agent (optional)
    custom_restrictions: list[str] | None = None  # Custom restrictions for the agent (optional)
    blocked_topics: list[str] | None = None  # Topics the agent should avoid (optional)
    blocked_keywords: list[str] | None = None  # Keywords the agent should avoid (optional)

    @classmethod
    def from_api_request(cls, request: dict) -> "RegisterAgentDTO":
        """Create DTO from API request data.
        
        Args:
            request: Dictionary with request data
            
        Returns:
            RegisterAgentDTO instance
        """
        return cls(
            name=request.get("name", ""),
            kind=request.get("kind", "business"),
            status=request.get("status", "inactive"),
            description=request.get("description", ""),
            is_published=request.get("is_published", False),
            config_type=request.get("config_type", "default"),
            business=request.get("business", ""),
            tools=request.get("tools"),
            workflows=request.get("workflows"),
            agents=request.get("agents"),
            knowledge_base=request.get("knowledge_base"),
            version=request.get("version", "1.0.0"),
            healthcheck_endpoint=request.get("healthcheck_endpoint"),
            invoke_endpoint=request.get("invoke_endpoint"),
            metadata=request.get("metadata"),
            model=request.get("model"),
            provider=request.get("provider"),
            temperature=request.get("temperature"),
            max_tokens=request.get("max_tokens"),
            timeout_ms=request.get("timeout_ms"),
            max_concurrency=request.get("max_concurrency"),
            retry_count=request.get("retry_count"),
            streaming_supported=request.get("streaming_supported"),
            custom_system_prompt=request.get("custom_system_prompt"),
            custom_instructions=request.get("custom_instructions"),
            custom_restrictions=request.get("custom_restrictions"),
            blocked_topics=request.get("blocked_topics"),
            blocked_keywords=request.get("blocked_keywords"),
        )
