"""Pytest fixtures for unit tests."""

import pytest
from uuid6 import uuid7

from app.layer1_domain.entities.agent import (
    Agent,
    AgentKind,
    AgentStatus,
    ConfigType,
)
from app.layer1_domain.entities.tool import Tool, ToolProtocol, ToolStatus
from app.layer1_domain.entities.workflow import Workflow


@pytest.fixture
def sample_agent_data():
    """Sample agent data for testing."""
    return {
        "id": str(uuid7()),
        "name": "test-agent",
        "kind": AgentKind.BUSINESS,
        "status": AgentStatus.ACTIVE,
        "is_published": True,
        "description": "A test agent",
        "healthcheck_endpoint": None,
        "invoke_endpoint": "https://agent.example.com/invoke",
        "version": "1.0.0",
        "provider": "openai",
        "model": "gpt-4",
        "temperature": 0.7,
        "max_tokens": 4096,
        "system_prompt": "You are a helpful assistant",
        "config_type": ConfigType.DEFAULT,
        "timeout_ms": 30000,
        "max_concurrency": 1,
        "retry_count": 3,
        "streaming_supported": False,
        "capabilities": ["coding", "analysis"],
        "tools": ["tool-1"],
        "workflows": ["workflow-1"],
        "agents": [],
        "knowledge_base": ["doc-1", "doc-2"],
        "metadata": {"env": "test"},
        "user_email": "owner@example.com",
        "tenant_id": "tenant-1",
        "custom_system_prompt": None,
        "custom_instructions": [],
        "custom_restrictions": [],
        "blocked_topics": [],
        "blocked_keywords": [],
        "user_roles": [],
    }


@pytest.fixture
def sample_technical_agent_data():
    """Sample technical agent data for testing."""
    return {
        "id": str(uuid7()),
        "name": "technical-agent",
        "kind": AgentKind.TECHNICAL,
        "status": AgentStatus.ACTIVE,
        "is_published": True,
        "description": "A technical agent",
        "healthcheck_endpoint": "https://agent.example.com/health",
        "invoke_endpoint": "https://agent.example.com/invoke",
        "version": "1.0.0",
        "provider": "openai",
        "model": "gpt-4",
        "temperature": 0.7,
        "max_tokens": 4096,
        "system_prompt": "You are a technical assistant",
        "config_type": ConfigType.DEFAULT,
        "timeout_ms": 30000,
        "max_concurrency": 1,
        "retry_count": 3,
        "streaming_supported": False,
        "capabilities": ["system-monitoring"],
        "tools": [],
        "workflows": [],
        "agents": [],
        "knowledge_base": ["kb-tech-1"],
        "metadata": {},
        "user_email": "owner@example.com",
        "tenant_id": "tenant-1",
        "custom_system_prompt": None,
        "custom_instructions": [],
        "custom_restrictions": [],
        "blocked_topics": [],
        "blocked_keywords": [],
        "user_roles": [],
    }


@pytest.fixture
def sample_tool_data():
    """Sample tool data for testing."""
    return {
        "id": str(uuid7()),
        "name": "test-tool",
        "description": "A test tool",
        "protocol": ToolProtocol.REST,
        "endpoint": "https://api.example.com/tool",
        "parameters_schema": {"type": "object", "properties": {}},
        "response_schema": {"type": "object", "properties": {}},
        "auth_config": {"type": "bearer"},
        "version": "1.0.0",
        "status": ToolStatus.ACTIVE,
        "metadata": {},
    }


@pytest.fixture
def sample_workflow_data():
    """Sample workflow data for testing."""
    return {
        "id": str(uuid7()),
        "name": "test-workflow",
        "description": "A test workflow",
        "version": "1.0.0",
        "status": "active",
        "main_flow": False,
    }
