"""Unit tests for ORM models structure and instantiation."""

from datetime import datetime, timezone

import pytest
from uuid6 import uuid7
from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, Index
from sqlalchemy.inspection import inspect

from app.layer4_infrastructure.persistence.models.agent_model import AgentModel
from app.layer4_infrastructure.persistence.models.tool_model import ToolModel
from app.layer4_infrastructure.persistence.models.workflow_model import WorkflowModel
from app.layer4_infrastructure.persistence.models.agent_tool_model import AgentToolModel
from app.layer4_infrastructure.persistence.models.agent_workflow_model import AgentWorkflowModel


class TestAgentModel:
    """Test AgentModel ORM structure and instantiation."""

    def test_table_name(self):
        """Test that table name is correctly set."""
        assert AgentModel.__tablename__ == "agents"

    def test_has_required_columns(self):
        """Test that model has all required columns."""
        columns = {col.name for col in inspect(AgentModel).columns}
        
        required_columns = {
            "id", "name", "kind", "status", "is_published",
            "description", "healthcheck_endpoint", "invoke_endpoint",
            "is_alive", "last_health_check_at", "version", "agent_metadata",
            "provider", "model", "temperature", "max_tokens", "system_prompt", "config_type",
            "timeout_ms", "max_concurrency", "retry_count", "streaming_supported",
            "capabilities", "attached_agent_ids", "knowledge_base", "user_email", "tenant_id",
            "custom_system_prompt", "custom_instructions", "custom_restrictions",
            "blocked_topics", "blocked_keywords", "user_roles",
            "created_at", "updated_at"
        }
        
        assert required_columns.issubset(columns)

    def test_has_indexes(self):
        """Test that required indexes are defined."""
        mapper = inspect(AgentModel)
        table = mapper.local_table
        indexes = {idx.name for idx in table.indexes if idx.name}
        
        # Check for custom composite indexes
        assert "ix_agents_kind_status" in indexes
        assert "ix_agents_is_alive_kind" in indexes

    def test_primary_key_column(self):
        """Test that id is the primary key."""
        primary_keys = [col.name for col in inspect(AgentModel).primary_key]
        assert primary_keys == ["id"]

    def test_nullable_constraints(self):
        """Test nullable constraints on critical columns."""
        columns = {col.name: col for col in inspect(AgentModel).columns}
        
        # Non-nullable fields
        assert columns["id"].nullable is False
        assert columns["name"].nullable is False
        assert columns["kind"].nullable is False
        assert columns["status"].nullable is False
        assert columns["is_published"].nullable is False
        assert columns["is_alive"].nullable is False
        
        # Nullable fields
        assert columns["description"].nullable is True
        assert columns["healthcheck_endpoint"].nullable is True
        assert columns["agent_metadata"].nullable is True
        assert columns["custom_system_prompt"].nullable is True
        assert columns["custom_instructions"].nullable is True
        assert columns["custom_restrictions"].nullable is True
        assert columns["blocked_topics"].nullable is True
        assert columns["blocked_keywords"].nullable is True
        assert columns["user_roles"].nullable is True

    def test_column_types(self):
        """Test that columns have correct types."""
        columns = {col.name: col for col in inspect(AgentModel).columns}
        
        assert isinstance(columns["id"].type, String)
        assert isinstance(columns["name"].type, String)
        assert isinstance(columns["description"].type, Text)
        assert isinstance(columns["is_published"].type, Boolean)
        assert isinstance(columns["temperature"].type, Float)
        assert isinstance(columns["max_tokens"].type, Integer)
        assert isinstance(columns["last_health_check_at"].type, DateTime)
        assert isinstance(columns["created_at"].type, DateTime)
        assert isinstance(columns["custom_system_prompt"].type, Text)
        assert isinstance(columns["custom_instructions"].type, Text)
        assert isinstance(columns["custom_restrictions"].type, Text)
        assert isinstance(columns["blocked_topics"].type, Text)
        assert isinstance(columns["blocked_keywords"].type, Text)
        assert isinstance(columns["user_roles"].type, Text)

    def test_instantiation(self):
        """Test that model can be instantiated with values."""
        agent = AgentModel(
            id=str(uuid7()),
            name="Test Agent",
            kind="business",
            status="active",
            is_published=False,
            is_alive=True,
            version="1.0.0",
            provider="openai",
            model="gpt-4",
            temperature=0.7,
            max_tokens=1000,
            config_type="default",
            timeout_ms=30000,
            max_concurrency=5,
            retry_count=3,
            streaming_supported=True,
            custom_system_prompt="Custom prompt",
            custom_instructions='["instruction1", "instruction2"]',
            custom_restrictions='["restriction1"]',
            blocked_topics='["topic1"]',
            blocked_keywords='["keyword1", "keyword2"]',
            user_roles='["admin", "editor"]',
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        
        assert agent.name == "Test Agent"
        assert agent.kind == "business"
        assert agent.is_published is False
        assert agent.temperature == 0.7
        assert agent.custom_system_prompt == "Custom prompt"
        assert agent.custom_instructions == '["instruction1", "instruction2"]'
        assert agent.custom_restrictions == '["restriction1"]'
        assert agent.blocked_topics == '["topic1"]'
        assert agent.blocked_keywords == '["keyword1", "keyword2"]'
        assert agent.user_roles == '["admin", "editor"]'


class TestToolModel:
    """Test ToolModel ORM structure and instantiation."""

    def test_table_name(self):
        """Test that table name is correctly set."""
        assert ToolModel.__tablename__ == "tools"

    def test_has_required_columns(self):
        """Test that model has all required columns."""
        columns = {col.name for col in inspect(ToolModel).columns}
        
        required_columns = {
            "id", "name", "description", "protocol", "endpoint",
            "parameters_schema", "response_schema", "auth_config",
            "version", "status", "entity_metadata",
            "created_at", "updated_at"
        }
        
        assert required_columns.issubset(columns)

    def test_primary_key_column(self):
        """Test that id is the primary key."""
        primary_keys = [col.name for col in inspect(ToolModel).primary_key]
        assert primary_keys == ["id"]

    def test_nullable_constraints(self):
        """Test nullable constraints on critical columns."""
        columns = {col.name: col for col in inspect(ToolModel).columns}
        
        assert columns["id"].nullable is False
        assert columns["name"].nullable is False
        assert columns["protocol"].nullable is False
        assert columns["version"].nullable is False
        assert columns["status"].nullable is False
        
        assert columns["description"].nullable is True
        assert columns["endpoint"].nullable is True

    def test_column_types(self):
        """Test that columns have correct types."""
        columns = {col.name: col for col in inspect(ToolModel).columns}
        
        assert isinstance(columns["id"].type, String)
        assert isinstance(columns["name"].type, String)
        assert isinstance(columns["protocol"].type, String)
        assert isinstance(columns["parameters_schema"].type, Text)
        assert isinstance(columns["created_at"].type, DateTime)

    def test_instantiation(self):
        """Test that model can be instantiated with values."""
        tool = ToolModel(
            id=str(uuid7()),
            name="Test Tool",
            description="A test tool",
            protocol="rest",
            endpoint="https://api.example.com",
            version="1.0.0",
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        
        assert tool.name == "Test Tool"
        assert tool.protocol == "rest"
        assert tool.status == "active"


class TestWorkflowModel:
    """Test WorkflowModel ORM structure and instantiation."""

    def test_table_name(self):
        """Test that table name is correctly set."""
        assert WorkflowModel.__tablename__ == "workflows"

    def test_has_required_columns(self):
        """Test that model has all required columns."""
        columns = {col.name for col in inspect(WorkflowModel).columns}
        
        required_columns = {
            "id", "name", "description", "version",
            "metadata", "input_schema", "output_schema", "status", "main_flow",
            "created_at", "updated_at"
        }
        
        assert required_columns.issubset(columns)

    def test_primary_key_column(self):
        """Test that id is the primary key."""
        primary_keys = [col.name for col in inspect(WorkflowModel).primary_key]
        assert primary_keys == ["id"]

    def test_nullable_constraints(self):
        """Test nullable constraints on critical columns."""
        columns = {col.name: col for col in inspect(WorkflowModel).columns}
        
        assert columns["id"].nullable is False
        assert columns["name"].nullable is False
        assert columns["version"].nullable is False
        assert columns["status"].nullable is False
        assert columns["main_flow"].nullable is False
        
        assert columns["description"].nullable is True
        assert columns["metadata"].nullable is True
        assert columns["input_schema"].nullable is True
        assert columns["output_schema"].nullable is True

    def test_column_types(self):
        """Test that columns have correct types."""
        columns = {col.name: col for col in inspect(WorkflowModel).columns}
        
        assert isinstance(columns["id"].type, String)
        assert isinstance(columns["name"].type, String)
        assert isinstance(columns["version"].type, String)
        assert isinstance(columns["status"].type, String)
        assert isinstance(columns["main_flow"].type, Boolean)
        assert isinstance(columns["metadata"].type, Text)
        assert isinstance(columns["input_schema"].type, Text)
        assert isinstance(columns["created_at"].type, DateTime)

    def test_default_values(self):
        """Test that default values are set correctly."""
        columns = {col.name: col for col in inspect(WorkflowModel).columns}
        
        # main_flow should have default False
        assert columns["main_flow"].default is not None

    def test_instantiation(self):
        """Test that model can be instantiated with values."""
        workflow = WorkflowModel(
            id=str(uuid7()),
            name="Test Workflow",
            description="A test workflow",
            version="1.0.0",
            status="active",
            main_flow=True,
            workflow_metadata='{"source": "test"}',
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        
        assert workflow.name == "Test Workflow"
        assert workflow.version == "1.0.0"
        assert workflow.main_flow is True
        assert workflow.workflow_metadata == '{"source": "test"}'


class TestAgentToolModel:
    """Test AgentToolModel junction table structure."""

    def test_table_name(self):
        """Test that table name is correctly set."""
        assert AgentToolModel.__tablename__ == "agent_tools"

    def test_has_required_columns(self):
        """Test that model has all required columns."""
        columns = {col.name for col in inspect(AgentToolModel).columns}
        
        required_columns = {"id", "agent_id", "tool_id", "created_at"}
        
        assert required_columns.issubset(columns)

    def test_has_unique_constraint_index(self):
        """Test that unique constraint index exists."""
        mapper = inspect(AgentToolModel)
        table = mapper.local_table
        indexes = {idx.name for idx in table.indexes if idx.name}
        
        assert "ix_agent_tool_unique" in indexes

    def test_foreign_keys(self):
        """Test that foreign keys are defined."""
        mapper = inspect(AgentToolModel)
        table = mapper.local_table
        foreign_keys = []
        for col in table.columns:
            foreign_keys.extend(col.foreign_keys)
        
        assert len(foreign_keys) == 2
        fk_columns = {fk.parent.name for fk in foreign_keys}
        assert "agent_id" in fk_columns
        assert "tool_id" in fk_columns

    def test_nullable_constraints(self):
        """Test nullable constraints on foreign keys."""
        columns = {col.name: col for col in inspect(AgentToolModel).columns}
        
        assert columns["agent_id"].nullable is False
        assert columns["tool_id"].nullable is False

    def test_instantiation(self):
        """Test that model can be instantiated with values."""
        agent_tool = AgentToolModel(
            id=str(uuid7()),
            agent_id=str(uuid7()),
            tool_id=str(uuid7()),
            created_at=datetime.now(timezone.utc),
        )
        
        assert agent_tool.agent_id is not None
        assert agent_tool.tool_id is not None


class TestAgentWorkflowModel:
    """Test AgentWorkflowModel junction table structure."""

    def test_table_name(self):
        """Test that table name is correctly set."""
        assert AgentWorkflowModel.__tablename__ == "agent_workflows"

    def test_has_required_columns(self):
        """Test that model has all required columns."""
        columns = {col.name for col in inspect(AgentWorkflowModel).columns}
        
        required_columns = {"id", "agent_id", "workflow_id", "created_at"}
        
        assert required_columns.issubset(columns)

    def test_has_unique_constraint_index(self):
        """Test that unique constraint index exists."""
        mapper = inspect(AgentWorkflowModel)
        table = mapper.local_table
        indexes = {idx.name for idx in table.indexes if idx.name}
        
        assert "ix_agent_workflow_unique" in indexes

    def test_foreign_keys(self):
        """Test that foreign keys are defined."""
        mapper = inspect(AgentWorkflowModel)
        table = mapper.local_table
        foreign_keys = []
        for col in table.columns:
            foreign_keys.extend(col.foreign_keys)
        
        assert len(foreign_keys) == 2
        fk_columns = {fk.parent.name for fk in foreign_keys}
        assert "agent_id" in fk_columns
        assert "workflow_id" in fk_columns

    def test_nullable_constraints(self):
        """Test nullable constraints on foreign keys."""
        columns = {col.name: col for col in inspect(AgentWorkflowModel).columns}
        
        assert columns["agent_id"].nullable is False
        assert columns["workflow_id"].nullable is False

    def test_instantiation(self):
        """Test that model can be instantiated with values."""
        agent_workflow = AgentWorkflowModel(
            id=str(uuid7()),
            agent_id=str(uuid7()),
            workflow_id=str(uuid7()),
            created_at=datetime.now(timezone.utc),
        )
        
        assert agent_workflow.agent_id is not None
        assert agent_workflow.workflow_id is not None


class TestModelToDict:
    """Test to_dict methods on models."""

    def test_agent_model_to_dict_method_exists(self):
        """Test that AgentModel has to_dict method."""
        assert hasattr(AgentModel, "to_dict")
        assert callable(getattr(AgentModel, "to_dict"))

    def test_tool_model_to_dict_method_exists(self):
        """Test that ToolModel has to_dict method."""
        assert hasattr(ToolModel, "to_dict")
        assert callable(getattr(ToolModel, "to_dict"))

    def test_workflow_model_to_dict_method_exists(self):
        """Test that WorkflowModel has to_dict method."""
        assert hasattr(WorkflowModel, "to_dict")
        assert callable(getattr(WorkflowModel, "to_dict"))

    def test_agent_tool_model_to_dict_method_exists(self):
        """Test that AgentToolModel has to_dict method."""
        assert hasattr(AgentToolModel, "to_dict")
        assert callable(getattr(AgentToolModel, "to_dict"))

    def test_agent_workflow_model_to_dict_method_exists(self):
        """Test that AgentWorkflowModel has to_dict method."""
        assert hasattr(AgentWorkflowModel, "to_dict")
        assert callable(getattr(AgentWorkflowModel, "to_dict"))
