"""Agent entity - Core business object representing an agent in the registry.

This is a pure domain entity with NO external dependencies.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from ipaddress import ip_address
from urllib.parse import urlparse

from app.layer1_domain.exceptions import (
    InvalidDataException,
    InvalidAgentKindException,
    InvalidAgentStatusException,
    InvalidAgentConfigTypeException,
)

# SA-1938: `provider`, `model` and `temperature` are chosen by the caller and only
# stored here.
#   * `provider` is a plain string, deliberately NOT a closed enum - the LLM gateway
#     routes 60+ OpenAI-compatible providers, so an allowlist would need a backend
#     release for every new one.
#   * None means "the caller did not supply one" and is a valid stored state. Nothing
#     in this service substitutes a default: an omitted value must stay omitted, or the
#     caller cannot tell its own choice apart from something the backend invented.


class AgentKind(Enum):
    """Agent type enumeration."""

    BUSINESS = "business"
    TECHNICAL = "technical"


class AgentStatus(Enum):
    """Agent status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"  # Soft delete status


class ConfigType(Enum):
    """Configuration type enumeration."""

    DEFAULT = "default"
    CUSTOM = "custom"


@dataclass
class Agent:
    """Agent entity representing a registered agent in the system.

    Domain invariants:
    - name must be 1-255 characters
    - temperature must be between 0.0 and 2.0
    - max_tokens must be > 0
    - timeout_ms must be > 0
    - max_concurrency must be >= 1
    - retry_count must be >= 0
    - agents cannot contain self reference
    """
    
    VALIDATORS = {
        "kind": "validate_kind",
        "status": "validate_status",
        "provider": "validate_provider",
        "config_type": "validate_config_type",
        "temperature": "validate_temperature",
        "max_tokens": "validate_max_tokens",
        "timeout_ms": "validate_timeout_ms",
        "max_concurrency": "validate_max_concurrency",
        "retry_count": "validate_retry_count",
        "healthcheck_endpoint": "validate_endpoint",
        "invoke_endpoint": "validate_endpoint",
    }

    id: str
    name: str
    kind: AgentKind
    status: AgentStatus
    is_published: bool
    description: str
    healthcheck_endpoint: str | None
    invoke_endpoint: str | None
    is_alive: bool
    last_health_check_at: datetime | None
    version: str
    provider: str | None
    model: str | None
    temperature: float | None
    max_tokens: int
    system_prompt: str
    config_type: ConfigType  # Configuration type: default or custom
    # Execution policy fields (merged from ExecutionPolicy)
    timeout_ms: int
    max_concurrency: int
    retry_count: int
    streaming_supported: bool
    # Capabilities and relationships
    capabilities: list[str] = field(default_factory=list)  # List of capability names/intents
    capability_summary: str = ""  # Concise, deduplicated, human-readable capability summary
    tools: list[str] = field(default_factory=list)  # List of tool IDs
    workflows: list[str] = field(default_factory=list)  # List of workflow IDs
    agents: list[str] = field(default_factory=list)  # List of attached agent IDs
    knowledge_base: list[str] = field(default_factory=list)  # List of document IDs
    metadata: dict = field(default_factory=dict)
    custom_system_prompt: str | None = None  # Optional custom system prompt
    custom_instructions: list[str] = field(default_factory=list)  # Optional custom instructions
    custom_restrictions: list[str] = field(default_factory=list)  # Optional custom restrictions
    blocked_topics: list[str] = field(default_factory=list)  # Optional blocked topics
    blocked_keywords: list[str] = field(default_factory=list)  # Optional blocked keywords
    user_roles: list[str] = field(default_factory=list)  # User user_roles for access control
    user_email: str | None = None  # Email of the user who registered the agent (optional)
    tenant_id: str | None = None  # ID of the tenant (optional)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __str__(self):
        """Return a string representation of the agent."""
        return f"Agent(id={self.id}, name={self.name}, kind={self.kind}, config_type={self.config_type}, version={self.version})"

    def is_available(self) -> bool:
        """Determine if the agent is available based on business rules.

        Business rules:
        - Technical agents: available if they respond to health check (is_alive)
        - Business agents: available if status is 'active'
        - Inactive agents are not available
        - Unpublished agents are not available
        """
        if not self.is_published:
            return False
        if not self.is_active():
            return False
        if self.is_technical() and not self.is_alive:
            return False

        return True

    def is_active(self) -> bool:
        """Check if agent is in active status."""
        return self.status == AgentStatus.ACTIVE

    def is_technical(self) -> bool:
        """Check if agent is a technical agent."""
        return self.kind == AgentKind.TECHNICAL

    def is_business(self) -> bool:
        """Check if agent is a business agent."""
        return self.kind == AgentKind.BUSINESS

    def is_deleted(self) -> bool:
        """Check if agent is marked as deleted."""
        return self.status == AgentStatus.DELETED

    def can_be_health_checked(self) -> bool:
        """Determine if this agent supports health checking.

        Any agent with a configured health check endpoint can be health-checked.
        """
        return self.healthcheck_endpoint is not None and self.is_technical()

    def validate_temperature(self) -> None:
        """Validate temperature value.

        SA-1938: temperature is optional. None means "the caller did not choose one",
        which is a meaningful state - some models (reasoning models, several Bedrock
        models) do not accept a temperature at all, so the registry must not invent one.

        Raises:
            InvalidDataException: If temperature not in range 0.0-2.0
        """
        if self.temperature is None:
            return
        if not (0.0 <= self.temperature <= 2.0):
            raise InvalidDataException(
                "Temperature must be between 0.0 and 2.0", field="temperature"
            )

    def validate_max_tokens(self) -> None:
        """Validate max_tokens value.

        Raises:
            InvalidDataException: If max_tokens <= 0
        """
        if self.max_tokens <= 0:
            raise InvalidDataException(
                "Max tokens must be greater than 0", field="max_tokens"
            )

    def validate_timeout_ms(self) -> None:
        """Validate timeout_ms value.

        Raises:
            InvalidDataException: If timeout_ms <= 0
        """
        if self.timeout_ms <= 0:
            raise InvalidDataException(
                "Timeout must be greater than 0", field="timeout_ms"
            )

    def validate_max_concurrency(self) -> None:
        """Validate max_concurrency value.

        Raises:
            InvalidDataException: If max_concurrency < 1
        """
        if self.max_concurrency < 1:
            raise InvalidDataException(
                "Max concurrency must be at least 1", field="max_concurrency"
            )

    def validate_retry_count(self) -> None:
        """Validate retry_count value.

        Raises:
            InvalidDataException: If retry_count < 0
        """
        if self.retry_count < 0:
            raise InvalidDataException(
                "Retry count cannot be negative", field="retry_count"
            )
            
    def validate_provider(self) -> None:
        """Validate provider value.

        SA-1938: the provider is supplied by the caller and only stored here, so there
        is no allowlist to check against. None means "the caller did not supply one" and
        is a valid stored state - the registry never invents a value. A blank string is
        still rejected: that is a malformed value, not an absent one.

        Raises:
            InvalidDataException: If provider is present but blank
        """
        if self.provider is None:
            return
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise InvalidDataException("Provider must be a non-empty string", field="provider")
        
    def validate_kind(self) -> None:
        """Validate agent kind.

        Raises:
            InvalidAgentKindException: If kind is not a valid AgentKind
        """
        if self.kind not in AgentKind:
            raise InvalidAgentKindException(self.kind)
        
    def validate_status(self) -> None:
        """Validate agent status.

        Raises:
            InvalidAgentStatusException: If status is not a valid AgentStatus
        """
        if self.status not in AgentStatus:
            raise InvalidAgentStatusException(self.status)

    def validate_config_type(self) -> None:
        """Validate agent config type.

        Raises:
            InvalidAgentConfigTypeException: If config type is not a valid ConfigType
        """
        if self.config_type not in ConfigType:
            raise InvalidAgentConfigTypeException(self.config_type)
    
    def validate_endpoint(self) -> None:
        """Validate agent endpoints against allowed outbound URL rules.

        Raises:
            InvalidDataException: If endpoint is not a valid URL or targets loopback
        """
        def _validate_url(url: str, field_name: str) -> None:
            parsed = urlparse(url)

            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise InvalidDataException(
                    f"{field_name} must be a valid URL using http:// or https://",
                    field=field_name
                )

            hostname = parsed.hostname
            if hostname is None:
                raise InvalidDataException(
                    f"{field_name} must include a valid host",
                    field=field_name,
                )

            normalized_hostname = hostname.lower()
            if normalized_hostname == "localhost" or normalized_hostname.endswith(".localhost"):
                raise InvalidDataException(
                    f"{field_name} cannot target loopback hosts",
                    field=field_name,
                )

            try:
                if ip_address(normalized_hostname).is_loopback:
                    raise InvalidDataException(
                        f"{field_name} cannot target loopback hosts",
                        field=field_name,
                    )
            except ValueError:
                pass

        if self.healthcheck_endpoint:
            _validate_url(self.healthcheck_endpoint, "healthcheck_endpoint")

        if self.invoke_endpoint:
            _validate_url(self.invoke_endpoint, "invoke_endpoint")

    def update_alive_status(self, is_alive: bool) -> None:
        """Update agent liveness status and last health-check time.

        Args:
            is_alive: Whether the agent responded to health check
        """
        self.is_alive = is_alive
        self.last_health_check_at = datetime.now(timezone.utc)

    def mark_alive(self) -> None:
        """Mark agent as alive (successful health check)."""
        self.update_alive_status(is_alive=True)

    def mark_dead(self) -> None:
        """Mark agent as not alive (failed health check)."""
        self.update_alive_status(is_alive=False)

    def attach_agent(self, agent_id: str) -> None:
        """Attach a child agent to this agent.

        Args:
            agent_id: ID of the agent to attach

        Raises:
            InvalidDataException: If agent_id is self or already attached
        """
        if agent_id == self.id:
            raise InvalidDataException(
                "Agent cannot attach itself", field="agents"
            )
        if agent_id in self.agents:
            raise InvalidDataException(
                f"Agent {agent_id} is already attached", field="agents"
            )
        self.agents.append(agent_id)
        self.updated_at = datetime.now(timezone.utc)
    
    def apply_publication_rules(self) -> None:
        """Validate the is_published field.
        
        Automatically set status to INACTIVE if is_published is set to False, because unpublished agents cannot be active.
        If is_published is True, the agent status can be either ACTIVE or INACTIVE.
        """
        if self.is_published == False and self.status == AgentStatus.ACTIVE:
            self.status = AgentStatus.INACTIVE
            self.updated_at = datetime.now(timezone.utc)

    def detach_agent(self, agent_id: str) -> None:
        """Detach a child agent from this agent.

        Args:
            agent_id: ID of the agent to detach

        Raises:
            InvalidDataException: If agent_id is not attached
        """
        if agent_id not in self.agents:
            raise InvalidDataException(
                f"Agent {agent_id} is not attached", field="agents"
            )
        self.agents.remove(agent_id)
        self.updated_at = datetime.now(timezone.utc)

    def update_attribute(self, field_name: str, value) -> bool:
        """Generic method to update an attribute with validation.

        Args:
            field_name: Name of the attribute to update
            value: New value for the attribute

        Raises:
            InvalidDataException: If field_name is invalid or validation fails
        """

        if not hasattr(self, field_name):
            raise InvalidDataException(f"Invalid field name: {field_name}", field=field_name)
        
        # Skip update if value is None or same as current (no-op)
        if value is None or getattr(self, field_name) == value:
            return False
        
        # Pre-process value
        if isinstance(value, str):
            value = value.strip()
        
        # Set the new value
        setattr(self, field_name, value)
        
        # Validate the field if it has a corresponding validator
        validator_method_name = self.VALIDATORS.get(field_name)
        if validator_method_name:
            validator_method = getattr(self, validator_method_name)
            validator_method()
        
        return True
    
    @classmethod
    def create(
        cls,
        id: str,
        description: str,
        name: str,
        model: str | None = None,
        provider: str | None = None,
        kind: AgentKind = AgentKind.BUSINESS,
        status: AgentStatus = AgentStatus.INACTIVE,
        temperature: float | None = None,
        max_tokens: int = 4096,
        system_prompt: str = "",
        config_type: ConfigType = ConfigType.DEFAULT,
        timeout_ms: int = 30000,
        max_concurrency: int = 1,
        retry_count: int = 3,
        streaming_supported: bool = False,
        version: str = "1.0.0",
        is_published: bool = False,
        healthcheck_endpoint: str | None = None,
        invoke_endpoint: str | None = None,
        last_health_check_at: datetime | None = None,
        capabilities: list[str] | None = None,
        capability_summary: str = "",
        agents: list[str] | None = None,
        tools: list[str] | None = None,
        workflows: list[str] | None = None,
        knowledge_base: list[str] | None = None,
        metadata: dict | None = None,
        user_email: str | None = None,
        tenant_id: str | None = None,
        custom_system_prompt: str | None = None,
        custom_instructions: list[str] | None = None,
        custom_restrictions: list[str] | None = None,
        blocked_topics: list[str] | None = None,
        blocked_keywords: list[str] | None = None,
        user_roles: list[str] | None = None,
    ) -> "Agent":
        """Factory method to create a new Agent with business rule validation.

        Args:
            name: Agent name (1-255 chars)
            kind: Agent type (business or technical)
            status: Agent status (active, inactive)
            description: Full agent description
            provider: LLM provider (OpenAI, Anthropic, Azure, etc.)
            model: Model identifier (e.g., "gpt-4", "claude-3-opus")
            temperature: Sampling temperature (default: 0.7, range: 0.0-2.0)
            max_tokens: Maximum tokens (default: 4096, must be > 0)
            system_prompt: System prompt for the LLM (default: "")
            config_type: Configuration type (default: ConfigType.DEFAULT)
            timeout_ms: Execution timeout in milliseconds (default: 30000)
            max_concurrency: Maximum concurrent executions (default: 1)
            retry_count: Number of retry attempts (default: 3)
            streaming_supported: Whether streaming is supported (default: False)
            version: Agent version (default: "1.0.0")
            is_published: Whether agent is published (default: False)
            healthcheck_endpoint: Health check endpoint (optional)
            invoke_endpoint: Agent invocation endpoint (optional)
            capabilities: List of capability names/intents (optional)
            agents: List of child agent IDs (optional)
            knowledge_base: List of knowledge base document IDs (optional)
            metadata: Additional metadata dict (optional)
            user_email: Email of the user who registered the agent (optional)
            tenant_id: ID of the tenant (optional)
            custom_system_prompt: Custom system prompt overriding the default (optional)
            custom_instructions: List of custom instructions for the agent (optional)
            custom_restrictions: List of custom restrictions for the agent (optional)
            blocked_topics: List of blocked topics the agent should avoid (optional)
            blocked_keywords: List of blocked keywords the agent should avoid (optional)
            user_roles: List of user user_roles for access control (optional)

        Returns:
            New Agent instance

        Raises:
            InvalidDataException: If validation fails
        """
        # Validate name
        if not name or not name.strip():
            raise InvalidDataException("Agent name cannot be empty", field="name")
        if len(name) > 255:
            raise InvalidDataException(
                "Agent name cannot exceed 255 characters", field="name"
            )

        # Validate model. SA-1938: None is allowed (the caller did not choose one and
        # the registry must not invent a value); a blank string is still malformed.
        if model is not None and not model.strip():
            raise InvalidDataException("Model cannot be empty", field="model")


        # Validate agents don't contain self
        attached_ids = agents or []
        if id in attached_ids:
            raise InvalidDataException(
                "Agent cannot attach itself", field="agents"
            )

        # Create agent instance
        agent = cls(
            id=id,
            name=name.strip(),
            kind=kind,
            status=status,
            is_published=is_published,
            description=description,
            healthcheck_endpoint=healthcheck_endpoint,
            invoke_endpoint=invoke_endpoint,
            is_alive=False,  # New agents start as not alive
            last_health_check_at=last_health_check_at,
            version=version,
            provider=provider.strip() if provider is not None else None,
            model=model.strip() if model is not None else None,
            temperature=temperature,
            max_tokens=max_tokens,
            system_prompt=system_prompt,
            config_type=config_type,
            timeout_ms=timeout_ms,
            max_concurrency=max_concurrency,
            retry_count=retry_count,
            streaming_supported=streaming_supported,
            capabilities=capabilities or [],
            capability_summary=capability_summary,
            tools=list(set(tools or [])),  # Remove duplicates
            workflows=list(set(workflows or [])),  # Remove duplicates
            agents=list(set(attached_ids)),  # Remove duplicates
            knowledge_base=list(set(knowledge_base or [])),  # Remove duplicates
            metadata=metadata or {},
            tenant_id=tenant_id,
            user_email=user_email,
            custom_system_prompt=custom_system_prompt,
            custom_instructions=custom_instructions or [],
            custom_restrictions=custom_restrictions or [],
            blocked_topics=blocked_topics or [],
            blocked_keywords=blocked_keywords or [],
            user_roles=user_roles or [],
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        
        # Create agent cannot be deleted, so no need to validate_deleted_status here
        if agent.is_deleted():
            raise InvalidAgentStatusException("New agent cannot be in deleted status")
        
        # Validate
        for field_name, value in agent.__dict__.items():
            validator_method_name = agent.VALIDATORS.get(field_name)
            if validator_method_name:
                validator_method = getattr(agent, validator_method_name)
                validator_method()

        # Business logic validation
        agent.apply_publication_rules() # Validate is_published and adjust status if needed

        return agent

    def update(self, **kwargs) -> bool:
        """Update agent fields with re-validation.

        Args:
            kwargs: Fields to update (same as create method, all optional)

        Raises:
            InvalidDataException: If validation fails
        """
        # Track if any field was actually updated
        updated = False

        # Update fields if provided
        for field_name, value in kwargs.items():
            updated = self.update_attribute(field_name, value) or updated

        # Business logic validation after updates
        self.apply_publication_rules() # Validate is_published and adjust status if needed
        
        # Update timestamp if any field was updated
        if updated:
            self.updated_at = datetime.now(timezone.utc)
            
        return updated
