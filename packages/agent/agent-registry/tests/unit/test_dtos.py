"""Unit tests for DTOs."""

import pytest

from app.layer1_domain.entities.agent import Agent
from app.layer2_application.dtos.agent_response_dto import AgentResponseDTO
from app.layer2_application.dtos.register_agent_dto import RegisterAgentDTO
from app.layer2_application.dtos.register_tool_dto import RegisterToolDTO
from app.layer2_application.dtos.update_agent_dto import UpdateAgentDTO


class TestAgentResponseDTO:
    """Test AgentResponseDTO."""

    def test_from_entity(self, sample_agent_data):
        """Test converting agent entity to DTO."""
        agent = Agent.create(**sample_agent_data)
        
        dto = AgentResponseDTO.from_entity(agent)
        
        assert dto.id == agent.id
        assert dto.name == agent.name
        assert dto.kind == "business"  # Converted to string
        assert dto.status == "active"  # Converted to string
        assert dto.is_published == agent.is_published
        assert dto.description == agent.description
        assert dto.version == agent.version
        assert dto.user_email == agent.user_email
        assert dto.tenant_id == agent.tenant_id
        assert dto.custom_system_prompt == agent.custom_system_prompt
        assert dto.custom_instructions == agent.custom_instructions
        assert dto.custom_restrictions == agent.custom_restrictions
        assert dto.blocked_topics == agent.blocked_topics
        assert dto.blocked_keywords == agent.blocked_keywords
        assert dto.user_roles == agent.user_roles

    def test_from_entities_multiple(self, sample_agent_data, sample_technical_agent_data):
        """Test converting multiple agent entities to DTOs."""
        agent1 = Agent.create(**sample_agent_data)
        agent2 = Agent.create(**sample_technical_agent_data)
        
        dtos = AgentResponseDTO.from_entities([agent1, agent2])
        
        assert len(dtos) == 2
        assert dtos[0].name == "test-agent"
        assert dtos[1].name == "technical-agent"

    def test_from_entities_empty_list(self):
        """Test converting empty list returns empty list."""
        dtos = AgentResponseDTO.from_entities([])
        assert dtos == []

    def test_dto_is_frozen(self, sample_agent_data):
        """Test that AgentResponseDTO is immutable."""
        agent = Agent.create(**sample_agent_data)
        dto = AgentResponseDTO.from_entity(agent)
        
        # Try to modify - should raise exception
        with pytest.raises(Exception):  # FrozenInstanceError
            dto.name = "modified-name"

    def test_dto_preserves_all_fields(self, sample_agent_data):
        """Test that DTO preserves all important fields from entity."""
        agent = Agent.create(**sample_agent_data)
        dto = AgentResponseDTO.from_entity(agent)
        
        assert dto.healthcheck_endpoint == agent.healthcheck_endpoint
        assert dto.invoke_endpoint == agent.invoke_endpoint
        assert dto.is_alive == agent.is_alive
        assert dto.last_health_check_at == agent.last_health_check_at
        assert dto.capabilities == agent.capabilities
        assert dto.capability_summary == agent.capability_summary
        assert dto.tools == agent.tools
        assert dto.workflows == agent.workflows
        assert dto.knowledge_base == agent.knowledge_base
        assert dto.business == agent.system_prompt
        assert dto.metadata == agent.metadata
        assert dto.user_email == agent.user_email
        assert dto.tenant_id == agent.tenant_id
        assert dto.custom_system_prompt == agent.custom_system_prompt
        assert dto.custom_instructions == agent.custom_instructions
        assert dto.custom_restrictions == agent.custom_restrictions
        assert dto.blocked_topics == agent.blocked_topics
        assert dto.blocked_keywords == agent.blocked_keywords
        assert dto.user_roles == agent.user_roles
        assert dto.created_at == agent.created_at
        assert dto.updated_at == agent.updated_at


class TestRegisterAgentDTO:
    """Test RegisterAgentDTO."""

    def test_create_with_minimal_data(self):
        """Test creating DTO with minimal required fields."""
        dto = RegisterAgentDTO(
            name="test-agent",
            kind="business",
            status="active",
            description="Test agent",
        )
        
        assert dto.name == "test-agent"
        assert dto.kind == "business"
        assert dto.status == "active"
        assert dto.description == "Test agent"
        assert dto.is_published is False  # default
        assert dto.version == "1.0.0"  # default

    def test_create_with_all_fields(self):
        """Test creating DTO with all fields."""
        dto = RegisterAgentDTO(
            name="test-agent",
            kind="technical",
            status="active",
            description="Technical agent",
            is_published=True,
            config_type="custom",
            business="System prompt",
            tools=["tool-1", "tool-2"],
            workflows=["workflow-1"],
            agents=["agent-1"],
            version="2.0.0",
            healthcheck_endpoint="http://localhost:8000/health",
            invoke_endpoint="http://localhost:8000/invoke",
            knowledge_base=["doc-1", "doc-2"],
            metadata={"env": "prod"},
            custom_system_prompt="Custom system prompt",
            custom_instructions=["instruction1", "instruction2"],
            custom_restrictions=["restriction1"],
            blocked_topics=["topic1"],
            blocked_keywords=["keyword1"],
        )
        
        assert dto.name == "test-agent"
        assert dto.is_published is True
        assert dto.config_type == "custom"
        assert dto.tools == ["tool-1", "tool-2"]
        assert dto.workflows == ["workflow-1"]
        assert dto.agents == ["agent-1"]
        assert dto.knowledge_base == ["doc-1", "doc-2"]
        assert dto.healthcheck_endpoint == "http://localhost:8000/health"
        assert dto.custom_system_prompt == "Custom system prompt"
        assert dto.custom_instructions == ["instruction1", "instruction2"]
        assert dto.custom_restrictions == ["restriction1"]
        assert dto.blocked_topics == ["topic1"]
        assert dto.blocked_keywords == ["keyword1"]

    def test_from_api_request(self):
        """Test creating DTO from API request data."""
        request = {
            "name": "api-agent",
            "kind": "business",
            "status": "active",
            "description": "From API",
            "is_published": True,
            "version": "1.5.0",
            "custom_system_prompt": "Custom prompt",
            "custom_instructions": ["instruction1"],
            "custom_restrictions": ["restriction1"],
            "blocked_topics": ["topic1"],
            "blocked_keywords": ["keyword1"],
        }
        
        dto = RegisterAgentDTO.from_api_request(request)
        
        assert dto.name == "api-agent"
        assert dto.kind == "business"
        assert dto.is_published is True
        assert dto.version == "1.5.0"
        assert dto.custom_system_prompt == "Custom prompt"
        assert dto.custom_instructions == ["instruction1"]
        assert dto.custom_restrictions == ["restriction1"]
        assert dto.blocked_topics == ["topic1"]
        assert dto.blocked_keywords == ["keyword1"]

    def test_from_api_request_with_defaults(self):
        """Test that from_api_request applies defaults for missing fields."""
        request = {
            "name": "minimal-agent",
            "description": "Minimal",
        }
        
        dto = RegisterAgentDTO.from_api_request(request)
        
        assert dto.name == "minimal-agent"
        assert dto.kind == "business"  # default
        assert dto.status == "inactive"  # default
        assert dto.is_published is False  # default
        assert dto.version == "1.0.0"  # default

    def test_dto_is_frozen(self):
        """Test that RegisterAgentDTO is immutable."""
        dto = RegisterAgentDTO(
            name="test",
            kind="business",
            status="active",
            description="Test",
        )
        
        with pytest.raises(Exception):  # FrozenInstanceError
            dto.name = "modified"


class TestUpdateAgentDTO:
    """Test UpdateAgentDTO."""

    def test_create_with_all_none(self):
        """Test creating DTO with all None values."""
        dto = UpdateAgentDTO()
        
        assert dto.name is None
        assert dto.description is None
        assert dto.kind is None
        assert dto.tools is None
        assert dto.agents is None
        assert dto.workflows is None
        assert dto.config_type is None
        assert dto.business is None
        assert dto.knowledge_base is None
        assert dto.metadata is None

    def test_create_with_all_none_new_fields(self):
        """Test creating DTO with all new custom fields set to None."""
        dto = UpdateAgentDTO()

        assert dto.custom_system_prompt is None
        assert dto.custom_instructions is None
        assert dto.custom_restrictions is None
        assert dto.blocked_topics is None
        assert dto.blocked_keywords is None

    def test_create_with_selective_fields(self):
        """Test creating DTO with only some fields set."""
        dto = UpdateAgentDTO(
            name="updated-name",
            description="Updated description",
        )
        
        assert dto.name == "updated-name"
        assert dto.description == "Updated description"
        assert dto.kind is None  # Not set
        assert dto.tools is None  # Not set

    def test_from_api_request(self):
        """Test creating DTO from API request data."""
        request = {
            "name": "updated-agent",
            "description": "Updated",
            "kind": "technical",
            "tools": ["tool-1"],
            "knowledge_base": ["doc-9"],
            "custom_system_prompt": "Updated prompt",
            "custom_instructions": ["instruction1"],
            "custom_restrictions": ["restriction1"],
            "blocked_topics": ["topic1"],
            "blocked_keywords": ["keyword1"],
        }
        
        dto = UpdateAgentDTO.from_api_request(request)
        
        assert dto.name == "updated-agent"
        assert dto.description == "Updated"
        assert dto.kind == "technical"
        assert dto.tools == ["tool-1"]
        assert dto.knowledge_base == ["doc-9"]
        assert dto.custom_system_prompt == "Updated prompt"
        assert dto.custom_instructions == ["instruction1"]
        assert dto.custom_restrictions == ["restriction1"]
        assert dto.blocked_topics == ["topic1"]
        assert dto.blocked_keywords == ["keyword1"]

    def test_from_api_request_maps_business_field(self):
        """Test that the business field is preserved on the DTO."""
        request = {
            "business": "You are a helpful assistant",
        }
        
        dto = UpdateAgentDTO.from_api_request(request)
        
        assert dto.business == "You are a helpful assistant"

    def test_from_api_request_empty(self):
        """Test creating DTO from empty request."""
        request = {}
        
        dto = UpdateAgentDTO.from_api_request(request)
        
        # All fields should be None
        assert dto.name is None
        assert dto.description is None
        assert dto.kind is None
        assert dto.business is None

    def test_dto_is_frozen(self):
        """Test that UpdateAgentDTO is immutable."""
        dto = UpdateAgentDTO(name="test")
        
        with pytest.raises(Exception):  # FrozenInstanceError
            dto.name = "modified"

    def test_update_with_tools_and_workflows(self):
        """Test updating with tools and workflows."""
        dto = UpdateAgentDTO(
            tools=["tool-1", "tool-2"],
            workflows=["workflow-1"],
            knowledge_base=["doc-1"],
        )
        
        assert dto.tools == ["tool-1", "tool-2"]
        assert dto.workflows == ["workflow-1"]
        assert dto.knowledge_base == ["doc-1"]

    def test_update_with_attached_agents(self):
        """Test updating with attached agents."""
        dto = UpdateAgentDTO(
            agents=["agent-1", "agent-2", "agent-3"],
        )
        
        assert dto.agents == ["agent-1", "agent-2", "agent-3"]


class TestRegisterToolDTO:
    """Test RegisterToolDTO."""

    def test_from_api_request_maps_schema_field_names(self):
        request = {
            "name": "api-tool",
            "description": "From API",
            "protocol": "rest",
            "input_data": {"type": "object", "properties": {"query": {"type": "string"}}},
            "output_data": {"type": "object", "properties": {"result": {"type": "string"}}},
            "auth_config": {"type": "none"},
            "metadata": {"inline": True},
        }

        dto = RegisterToolDTO.from_api_request(request)

        assert dto.name == "api-tool"
        assert dto.parameters_schema == request["input_data"]
        assert dto.response_schema == request["output_data"]
        assert dto.auth_config == request["auth_config"]
        assert dto.metadata == request["metadata"]

    def test_from_api_request_keeps_legacy_schema_field_names(self):
        request = {
            "name": "legacy-tool",
            "description": "Legacy payload",
            "protocol": "rest",
            "input_data": {"type": "object"},
            "output_data": {"type": "object"},
        }

        dto = RegisterToolDTO.from_api_request(request)

        assert dto.parameters_schema == request["input_data"]
        assert dto.response_schema == request["output_data"]
