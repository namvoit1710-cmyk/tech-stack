"""Unit tests for Agent entity."""

import pytest
from datetime import datetime, timezone
from uuid6 import uuid7

from app.layer1_domain.entities.agent import (
    Agent,
    AgentKind,
    AgentStatus,
    ConfigType,
)
from app.layer1_domain.exceptions import (
    InvalidDataException,
)


class TestAgentEntity:
    """Test Agent entity business logic."""

    def test_agent_creation(self, sample_agent_data):
        """Test creating a basic agent."""
        agent = Agent.create(**sample_agent_data)
        assert agent.name == "test-agent"
        assert agent.kind == AgentKind.BUSINESS
        assert agent.status == AgentStatus.ACTIVE
        assert agent.is_published is True

    def test_is_active(self, sample_agent_data):
        """Test is_active method."""
        agent = Agent.create(**sample_agent_data)
        assert agent.is_active() is True

        agent.status = AgentStatus.INACTIVE
        assert agent.is_active() is False

    def test_is_technical(self, sample_agent_data):
        """Test is_technical method."""
        agent = Agent.create(**sample_agent_data)
        assert agent.is_technical() is False

        agent.kind = AgentKind.TECHNICAL
        assert agent.is_technical() is True

    def test_is_business(self, sample_agent_data):
        """Test is_business method."""
        agent = Agent.create(**sample_agent_data)
        assert agent.is_business() is True

        agent.kind = AgentKind.TECHNICAL
        assert agent.is_business() is False

    def test_can_be_health_checked_without_endpoint(self, sample_agent_data):
        """Test that agents without a healthcheck endpoint cannot be health checked."""
        agent = Agent.create(**sample_agent_data)
        assert agent.can_be_health_checked() is False

    def test_can_be_health_checked_business_agent_with_endpoint(self, sample_agent_data):
        """Test that business agents with an endpoint cannot be health checked."""
        sample_agent_data["healthcheck_endpoint"] = "https://agent.example.com/health"

        agent = Agent.create(**sample_agent_data)

        assert agent.can_be_health_checked() is False

    def test_can_be_health_checked_technical_agent(self, sample_technical_agent_data):
        """Test that technical agents with endpoint can be health checked."""
        agent = Agent.create(**sample_technical_agent_data)
        assert agent.can_be_health_checked() is True

        # Remove healthcheck endpoint
        agent.healthcheck_endpoint = None
        assert agent.can_be_health_checked() is False
        
    def test_any_provider_is_accepted(self, sample_agent_data):
        """SA-1938: the provider is caller-chosen and only stored - no allowlist."""
        sample_agent_data["provider"] = "aiml"

        agent = Agent.create(**sample_agent_data)

        assert agent.provider == "aiml"

    def test_blank_provider_is_rejected(self, sample_agent_data):
        """A provider must still be present - only the allowlist was dropped."""
        with pytest.raises(InvalidDataException) as exc_info:
            sample_agent_data["provider"] = "   "
            Agent.create(**sample_agent_data)
        assert "provider" in str(exc_info.value).lower()

    def test_business_agent_without_endpoint_is_created(self, sample_agent_data):
        """Test that business agents can be created without a healthcheck endpoint."""
        agent = Agent.create(**sample_agent_data)

        assert agent.healthcheck_endpoint is None
        assert agent.can_be_health_checked() is False

    def test_technical_agent_with_endpoint_is_health_checkable(
        self, sample_technical_agent_data
    ):
        """Test that technical agents with a healthcheck endpoint are health-checkable."""
        agent = Agent.create(**sample_technical_agent_data)

        assert agent.can_be_health_checked() is True

    def test_technical_agent_without_endpoint_is_allowed_but_not_health_checkable(
        self, sample_technical_agent_data
    ):
        """Test that technical agents may exist without a healthcheck endpoint."""
        sample_technical_agent_data["healthcheck_endpoint"] = None

        agent = Agent.create(**sample_technical_agent_data)

        assert agent.healthcheck_endpoint is None
        assert agent.can_be_health_checked() is False

    def test_validate_temperature_valid(self, sample_agent_data):
        """Test temperature validation with valid values."""
        agent = Agent.create(**sample_agent_data)
        agent.temperature = 0.0
        agent.validate_temperature()  # Should not raise

        agent.temperature = 1.0
        agent.validate_temperature()  # Should not raise

        agent.temperature = 2.0
        agent.validate_temperature()  # Should not raise

    def test_validate_temperature_invalid(self, sample_agent_data):
        """Test temperature validation with invalid values."""
        agent = Agent.create(**sample_agent_data)
        
        agent.temperature = -0.1
        with pytest.raises(InvalidDataException) as exc_info:
            agent.validate_temperature()
        assert "Temperature" in str(exc_info.value)

        agent.temperature = 2.1
        with pytest.raises(InvalidDataException):
            agent.validate_temperature()

    def test_validate_max_tokens_valid(self, sample_agent_data):
        """Test max_tokens validation with valid values."""
        agent = Agent.create(**sample_agent_data)
        agent.max_tokens = 1
        agent.validate_max_tokens()  # Should not raise

        agent.max_tokens = 100000
        agent.validate_max_tokens()  # Should not raise

    def test_validate_max_tokens_invalid(self, sample_agent_data):
        """Test max_tokens validation with invalid values."""
        agent = Agent.create(**sample_agent_data)
        agent.max_tokens = 0
        with pytest.raises(InvalidDataException) as exc_info:
            agent.validate_max_tokens()
        assert "Max tokens" in str(exc_info.value)

        agent.max_tokens = -1
        with pytest.raises(InvalidDataException):
            agent.validate_max_tokens()

    def test_validate_timeout_ms_valid(self, sample_agent_data):
        """Test timeout_ms validation with valid values."""
        agent = Agent.create(**sample_agent_data)
        agent.timeout_ms = 1
        agent.validate_timeout_ms()  # Should not raise

        agent.timeout_ms = 60000
        agent.validate_timeout_ms()  # Should not raise

    def test_validate_timeout_ms_invalid(self, sample_agent_data):
        """Test timeout_ms validation with invalid values."""
        agent = Agent.create(**sample_agent_data)
        agent.timeout_ms = 0
        with pytest.raises(InvalidDataException) as exc_info:
            agent.validate_timeout_ms()
        assert "Timeout" in str(exc_info.value)

        agent.timeout_ms = -1
        with pytest.raises(InvalidDataException):
            agent.validate_timeout_ms()

    def test_validate_max_concurrency_valid(self, sample_agent_data):
        """Test max_concurrency validation with valid values."""
        agent = Agent.create(**sample_agent_data)
        agent.max_concurrency = 1
        agent.validate_max_concurrency()  # Should not raise

        agent.max_concurrency = 10
        agent.validate_max_concurrency()  # Should not raise

    def test_validate_max_concurrency_invalid(self, sample_agent_data):
        """Test max_concurrency validation with invalid values."""
        agent = Agent.create(**sample_agent_data)
        agent.max_concurrency = 0
        with pytest.raises(InvalidDataException) as exc_info:
            agent.validate_max_concurrency()
        assert "Max concurrency" in str(exc_info.value)

        agent.max_concurrency = -1
        with pytest.raises(InvalidDataException):
            agent.validate_max_concurrency()

    def test_validate_retry_count_valid(self, sample_agent_data):
        """Test retry_count validation with valid values."""
        agent = Agent.create(**sample_agent_data)
        agent.retry_count = 0
        agent.validate_retry_count()  # Should not raise

        agent.retry_count = 5
        agent.validate_retry_count()  # Should not raise

    def test_validate_retry_count_invalid(self, sample_agent_data):
        """Test retry_count validation with invalid values."""
        agent = Agent.create(**sample_agent_data)
        agent.retry_count = -1
        with pytest.raises(InvalidDataException) as exc_info:
            agent.validate_retry_count()
        assert "Retry count" in str(exc_info.value)

    @pytest.mark.parametrize(
        ("field_name", "endpoint"),
        [
            ("healthcheck_endpoint", "ftp://agent.example.com/health"),
            ("invoke_endpoint", "ws://agent.example.com/invoke"),
        ],
    )
    def test_validate_endpoint_rejects_non_http_schemes(
        self, sample_technical_agent_data, field_name, endpoint
    ):
        """Test that agent endpoints only allow http and https."""
        sample_technical_agent_data[field_name] = endpoint

        with pytest.raises(InvalidDataException) as exc_info:
            Agent.create(**sample_technical_agent_data)

        assert field_name in str(exc_info.value)

    @pytest.mark.parametrize(
        ("field_name", "endpoint"),
        [
            ("healthcheck_endpoint", "http://localhost:8000/health"),
            ("healthcheck_endpoint", "http://127.0.0.1:8000/health"),
            ("invoke_endpoint", "http://127.0.0.1:8000/invoke"),
            ("healthcheck_endpoint", "https://[::1]/health"),
            ("invoke_endpoint", "https://[::1]/invoke"),
        ],
    )
    def test_validate_endpoint_rejects_loopback_hosts(
        self, sample_technical_agent_data, field_name, endpoint
    ):
        """Test that agent endpoints reject loopback hosts."""
        sample_technical_agent_data[field_name] = endpoint

        with pytest.raises(InvalidDataException) as exc_info:
            Agent.create(**sample_technical_agent_data)

        assert field_name in str(exc_info.value)

    def test_update_alive_status(self, sample_agent_data):
        """Test updating alive status."""
        agent = Agent.create(**sample_agent_data)
        original_health_check_at = agent.last_health_check_at

        agent.update_alive_status(is_alive=True)
        assert agent.is_alive is True
        assert original_health_check_at is None
        assert agent.last_health_check_at is not None
        assert agent.last_health_check_at.tzinfo == timezone.utc

        agent.update_alive_status(is_alive=False)
        assert agent.is_alive is False
        assert agent.last_health_check_at is not None
        assert agent.last_health_check_at.tzinfo == timezone.utc

    def test_mark_alive(self, sample_agent_data):
        """Test marking agent as alive."""
        agent = Agent.create(**sample_agent_data)
        agent.is_alive = False

        agent.mark_alive()
        assert agent.is_alive is True
        assert agent.last_health_check_at is not None
        assert agent.last_health_check_at.tzinfo == timezone.utc

    def test_mark_dead(self, sample_agent_data):
        """Test marking agent as dead."""
        agent = Agent.create(**sample_agent_data)
        agent.is_alive = True

        agent.mark_dead()
        assert agent.is_alive is False
        assert agent.last_health_check_at is not None
        assert agent.last_health_check_at.tzinfo == timezone.utc

    def test_attach_agent_success(self, sample_agent_data):
        """Test attaching a child agent successfully."""
        agent = Agent.create(**sample_agent_data)
        child_id = str(uuid7())

        agent.attach_agent(child_id)
        assert child_id in agent.agents
        assert len(agent.agents) == 1

    def test_attach_agent_self_reference(self, sample_agent_data):
        """Test that agent cannot attach itself."""
        agent = Agent.create(**sample_agent_data)
        
        with pytest.raises(InvalidDataException) as exc_info:
            agent.attach_agent(agent.id)
        assert "cannot attach itself" in str(exc_info.value)

    def test_attach_agent_already_attached(self, sample_agent_data):
        """Test that same agent cannot be attached twice."""
        agent = Agent.create(**sample_agent_data)
        child_id = str(uuid7())
        
        agent.attach_agent(child_id)
        
        with pytest.raises(InvalidDataException) as exc_info:
            agent.attach_agent(child_id)
        assert "already attached" in str(exc_info.value)

    def test_detach_agent_success(self, sample_agent_data):
        """Test detaching a child agent successfully."""
        agent = Agent.create(**sample_agent_data)
        child_id = str(uuid7())
        
        agent.attach_agent(child_id)
        agent.detach_agent(child_id)
        
        assert child_id not in agent.agents
        assert len(agent.agents) == 0

    def test_detach_agent_not_attached(self, sample_agent_data):
        """Test that detaching non-attached agent fails."""
        agent = Agent.create(**sample_agent_data)
        child_id = str(uuid7())
        
        with pytest.raises(InvalidDataException) as exc_info:
            agent.detach_agent(child_id)
        assert "not attached" in str(exc_info.value)

    def test_create_agent_with_valid_data(self):
        """Test creating agent with valid data using factory method."""
        agent_id = str(uuid7())
        agent = Agent.create(
            id=agent_id,
            name="test-agent",
            kind=AgentKind.BUSINESS,
            status=AgentStatus.ACTIVE,
            description="Test agent",
            provider="openai",
            model="gpt-4",
            knowledge_base=["doc-1"],
        )
        
        assert agent.id == agent_id
        assert agent.name == "test-agent"
        assert agent.kind == AgentKind.BUSINESS
        assert agent.status == AgentStatus.INACTIVE
        assert agent.is_published is False
        assert agent.temperature is None  # SA-1938: unset stays unset, no invented default
        assert agent.max_tokens == 4096  # default
        assert agent.knowledge_base == ["doc-1"]
        assert agent.custom_system_prompt is None
        assert agent.custom_instructions == []
        assert agent.custom_restrictions == []
        assert agent.blocked_topics == []
        assert agent.blocked_keywords == []
        assert agent.user_roles == []

    def test_create_agent_with_custom_fields(self):
        """Test creating agent with custom fields."""
        agent_id = str(uuid7())
        agent = Agent.create(
            id=agent_id,
            name="custom-agent",
            kind=AgentKind.BUSINESS,
            status=AgentStatus.ACTIVE,
            description="Custom agent",
            provider="openai",
            model="gpt-4",
            custom_system_prompt="Custom system prompt",
            custom_instructions=["instruction1", "instruction2"],
            custom_restrictions=["restriction1"],
            blocked_topics=["topic1"],
            blocked_keywords=["keyword1", "keyword2"],
            user_roles=["admin", "editor"],
        )

        assert agent.custom_system_prompt == "Custom system prompt"
        assert agent.custom_instructions == ["instruction1", "instruction2"]
        assert agent.custom_restrictions == ["restriction1"]
        assert agent.blocked_topics == ["topic1"]
        assert agent.blocked_keywords == ["keyword1", "keyword2"]
        assert agent.user_roles == ["admin", "editor"]

    def test_update_agent_custom_fields(self):
        """Test updating custom fields via agent.update()."""
        agent_id = str(uuid7())
        agent = Agent.create(
            id=agent_id,
            name="update-custom-agent",
            kind=AgentKind.BUSINESS,
            status=AgentStatus.ACTIVE,
            description="Agent for update test",
            provider="openai",
            model="gpt-4",
        )

        assert agent.custom_system_prompt is None
        assert agent.custom_instructions == []
        assert agent.custom_restrictions == []
        assert agent.blocked_topics == []
        assert agent.blocked_keywords == []

        agent.update(
            custom_system_prompt="Updated prompt",
            custom_instructions=["inst1"],
            custom_restrictions=["rest1"],
            blocked_topics=["topic1"],
            blocked_keywords=["key1"],
        )

        assert agent.custom_system_prompt == "Updated prompt"
        assert agent.custom_instructions == ["inst1"]
        assert agent.custom_restrictions == ["rest1"]
        assert agent.blocked_topics == ["topic1"]
        assert agent.blocked_keywords == ["key1"]

    def test_create_agent_with_empty_name(self):
        """Test that creating agent with empty name fails."""
        with pytest.raises(InvalidDataException) as exc_info:
            Agent.create(
                id=str(uuid7()),
                name="",
                kind=AgentKind.BUSINESS,
                status=AgentStatus.ACTIVE,
                description="Test",
                provider="openai",
                model="gpt-4",
            )
        assert "name cannot be empty" in str(exc_info.value)

    def test_create_agent_with_long_name(self):
        """Test that creating agent with name > 255 chars fails."""
        with pytest.raises(InvalidDataException) as exc_info:
            Agent.create(
                id=str(uuid7()),
                name="a" * 256,
                kind=AgentKind.BUSINESS,
                status=AgentStatus.ACTIVE,
                description="Test",
                provider="openai",
                model="gpt-4",
            )
        assert "cannot exceed 255 characters" in str(exc_info.value)

    def test_create_agent_with_empty_model(self):
        """Test that creating agent with empty model fails."""
        with pytest.raises(InvalidDataException) as exc_info:
            Agent.create(
                id=str(uuid7()),
                name="test-agent",
                kind=AgentKind.BUSINESS,
                status=AgentStatus.ACTIVE,
                description="Test",
                provider="openai",
                model="",
            )
        assert "Model cannot be empty" in str(exc_info.value)

    def test_create_technical_agent_without_endpoint(self):
        """Test that creating technical agent without endpoint succeeds."""
        agent = Agent.create(
            id=str(uuid7()),
            name="tech-agent",
            kind=AgentKind.TECHNICAL,
            status=AgentStatus.ACTIVE,
            description="Technical agent",
            provider="openai",
            model="gpt-4",
            healthcheck_endpoint=None,
        )

        assert agent.kind == AgentKind.TECHNICAL
        assert agent.healthcheck_endpoint is None
        assert agent.can_be_health_checked() is False

    def test_create_technical_agent_with_endpoint(self):
        """Test creating technical agent with endpoint succeeds."""
        agent_id = str(uuid7())
        agent = Agent.create(
            id=agent_id,
            name="tech-agent",
            kind=AgentKind.TECHNICAL,
            status=AgentStatus.ACTIVE,
            description="Technical agent",
            provider="openai",
            model="gpt-4",
            healthcheck_endpoint="https://agent.example.com/health",
        )
        
        assert agent.id == agent_id
        assert agent.kind == AgentKind.TECHNICAL
        assert agent.healthcheck_endpoint == "https://agent.example.com/health"
        assert agent.can_be_health_checked() is True
