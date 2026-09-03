from unittest.mock import AsyncMock, MagicMock

import pytest


def _mock_tool_factory(capability, registry):
    """A simple mock tool factory for testing."""
    tool = MagicMock()
    tool.name = f"call_{capability.agent_type.replace('-', '_')}"
    tool.remote_agent_type = capability.agent_type
    return tool


@pytest.mark.asyncio
async def test_discover_returns_capabilities_and_tools():
    """discover() should return AgentCapability list and tool list via tool_factory."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-1",
                "name": "processor-1.0",
                "description": "Processes files",
                "domain": "files",
                "configuration": {
                    "agent_type": "file-processor",
                    "endpoint_url": "http://proc:8000",
                    "routing": {
                        "input_schema": {
                            "type": "object",
                            "properties": {"file_id": {"type": "string"}},
                        },
                        "output_schema": {"type": "object"},
                        "required_parameters": ["file_id"],
                        "negative_examples": ["general questions"],
                        "timeout_seconds": 60.0,
                    },
                },
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry, tool_factory=_mock_tool_factory)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    cap = capabilities[0]
    assert cap.agent_type == "file-processor"
    assert cap.name == "processor-1.0"
    assert cap.description == "Processes files"
    assert cap.input_schema["properties"]["file_id"]["type"] == "string"
    assert cap.required_parameters == ["file_id"]
    assert cap.negative_examples == ["general questions"]
    assert cap.timeout_seconds == 60.0
    assert cap.enabled is True

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "call_file_processor"
    assert tool.remote_agent_type == "file-processor"


@pytest.mark.asyncio
async def test_discover_without_tool_factory_returns_empty_tools():
    """discover() without tool_factory should return capabilities but empty tools list."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-1",
                "name": "processor-1.0",
                "description": "Processes files",
                "domain": "files",
                "configuration": {
                    "agent_type": "file-processor",
                    "endpoint_url": "http://proc:8000",
                },
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry)  # No tool_factory
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    assert len(tools) == 0


@pytest.mark.asyncio
async def test_discover_agent_without_routing_metadata():
    """Agents without routing metadata should still be discovered with defaults."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-2",
                "name": "simple-agent-1.0",
                "description": "A simple agent",
                "domain": "general",
                "configuration": {
                    "agent_type": "simple-agent",
                    "endpoint_url": "http://simple:8000",
                },
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry, tool_factory=_mock_tool_factory)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    cap = capabilities[0]
    assert cap.agent_type == "simple-agent"
    assert cap.description == "A simple agent"
    assert cap.input_schema == {"type": "object", "properties": {}}
    assert cap.required_parameters == []
    assert cap.negative_examples == []

    assert len(tools) == 1
    assert tools[0].name == "call_simple_agent"


@pytest.mark.asyncio
async def test_discover_with_domain_filter():
    """discover() should pass domain filter to registry."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(return_value=[])

    service = AgentDiscoveryService(registry=registry)
    await service.discover(domain="finance")

    registry.list_active_agents.assert_awaited_once_with(domain="finance")


@pytest.mark.asyncio
async def test_discover_skips_agents_without_agent_type():
    """Agents missing agent_type in configuration should be skipped."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-bad",
                "name": "broken",
                "description": "Missing agent_type",
                "domain": "test",
                "configuration": {},
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry, tool_factory=_mock_tool_factory)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 0
    assert len(tools) == 0


@pytest.mark.asyncio
async def test_discover_excludes_self():
    """discover() should exclude the supervisor's own agent_type if specified."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-self",
                "name": "supervisor-1.0",
                "description": "I am the supervisor",
                "domain": "test",
                "configuration": {"agent_type": "supervisor"},
            },
            {
                "agent_id": "uuid-sub",
                "name": "worker-1.0",
                "description": "I am a worker",
                "domain": "test",
                "configuration": {"agent_type": "worker"},
            },
        ]
    )

    service = AgentDiscoveryService(
        registry=registry,
        exclude_agent_types=["supervisor"],
        tool_factory=_mock_tool_factory,
    )
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].agent_type == "worker"


@pytest.mark.asyncio
async def test_discover_uses_top_level_agent_type_and_routing():
    """discover() should use top-level agent_type and routing fields (new format)."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-top",
                "name": "processor-2.0",
                "description": "Processes files v2",
                "domain": "files",
                "agent_type": "file-processor",
                "routing": {
                    "input_schema": {
                        "type": "object",
                        "properties": {"file_id": {"type": "string"}},
                    },
                    "output_schema": {"type": "object"},
                    "required_parameters": ["file_id"],
                    "negative_examples": ["general questions"],
                    "timeout_seconds": 90.0,
                },
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry, tool_factory=_mock_tool_factory)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    cap = capabilities[0]
    assert cap.agent_type == "file-processor"
    assert cap.name == "processor-2.0"
    assert cap.description == "Processes files v2"
    assert cap.input_schema["properties"]["file_id"]["type"] == "string"
    assert cap.required_parameters == ["file_id"]
    assert cap.negative_examples == ["general questions"]
    assert cap.timeout_seconds == 90.0
    assert cap.enabled is True

    assert len(tools) == 1
    assert tools[0].name == "call_file_processor"


@pytest.mark.asyncio
async def test_discover_top_level_agent_type_takes_precedence_over_configuration():
    """Top-level agent_type should take precedence over configuration.agent_type."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-both",
                "name": "agent-both-formats",
                "description": "Has both formats",
                "domain": "test",
                "agent_type": "top-level-type",
                "routing": {
                    "timeout_seconds": 120.0,
                },
                "configuration": {
                    "agent_type": "nested-type",
                    "routing": {
                        "timeout_seconds": 60.0,
                    },
                },
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].agent_type == "top-level-type"
    assert capabilities[0].timeout_seconds == 120.0


@pytest.mark.asyncio
async def test_discover_falls_back_to_configuration_when_no_top_level_agent_type():
    """discover() should fall back to configuration.agent_type when no top-level agent_type."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-fallback",
                "name": "legacy-agent-1.0",
                "description": "Uses old format",
                "domain": "legacy",
                "configuration": {
                    "agent_type": "legacy-type",
                    "routing": {
                        "input_schema": {"type": "object", "properties": {}},
                        "timeout_seconds": 45.0,
                    },
                },
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].agent_type == "legacy-type"
    assert capabilities[0].timeout_seconds == 45.0


@pytest.mark.asyncio
async def test_discover_skips_agents_without_agent_type_in_either_location():
    """Agents missing agent_type in both top-level and configuration should be skipped."""
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "uuid-no-type",
                "name": "broken-agent",
                "description": "No agent_type anywhere",
                "domain": "test",
            },
        ]
    )

    service = AgentDiscoveryService(registry=registry)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 0
    assert len(tools) == 0


@pytest.mark.asyncio
async def test_discover_current_registry_payload_falls_back_to_name_and_defaults():
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "id": "agent-123",
                "name": "planner",
                "description": "Planner agent",
                "invoke_endpoint": "http://planner:36000/api/v1/execute",
                "healthcheck_endpoint": "http://planner:36000/health",
                "metadata": {},
            }
        ]
    )

    service = AgentDiscoveryService(registry=registry, tool_factory=_mock_tool_factory)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].agent_type == "planner"
    assert capabilities[0].name == "planner"
    assert capabilities[0].description == "Planner agent"
    assert capabilities[0].input_schema == {"type": "object", "properties": {}}
    assert capabilities[0].required_parameters == []
    assert capabilities[0].negative_examples == []
    assert capabilities[0].timeout_seconds == 300.0
    assert len(tools) == 1
    assert tools[0].remote_agent_type == "planner"


@pytest.mark.asyncio
async def test_discover_prefers_metadata_agent_type_routing_and_queue_metadata():
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "id": "agent-456",
                "name": "planner-service",
                "description": "Planner service",
                "invoke_endpoint": "http://planner:36000/api/v1/execute",
                "metadata": {
                    "agent_type": "planner",
                    "routing": {
                        "input_schema": {
                            "type": "object",
                            "properties": {"task": {"type": "string"}},
                        },
                        "required_parameters": ["task"],
                        "timeout_seconds": 45.0,
                    },
                    "queue_metadata": {
                        "queue_name": "planner.queue",
                        "request_topic": "planner.request",
                        "reply_topic": "planner.reply",
                    },
                },
            }
        ]
    )

    service = AgentDiscoveryService(registry=registry)
    capabilities, _ = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].agent_type == "planner"
    assert capabilities[0].input_schema["properties"]["task"]["type"] == "string"
    assert capabilities[0].required_parameters == ["task"]
    assert capabilities[0].timeout_seconds == 45.0
    assert capabilities[0].queue_metadata.queue_name == "planner.queue"


@pytest.mark.asyncio
async def test_build_capability_preserves_top_level_registry_validation_fields():
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_capabilities = AsyncMock(
        return_value=[
            {
                "agent_type": "planner",
                "name": "planner",
                "description": "Planner agent",
                "invoke_endpoint": "http://planner:36000/api/v1/execute",
                "input_schema": {
                    "type": "object",
                    "properties": {"task": {"type": "string"}},
                    "required": ["task"],
                },
                "required_parameters": ["task"],
                "timeout_seconds": 45.0,
            }
        ]
    )

    service = AgentDiscoveryService(registry=registry)
    capabilities, _ = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].input_schema["properties"]["task"]["type"] == "string"
    assert capabilities[0].required_parameters == ["task"]
    assert capabilities[0].timeout_seconds == 45.0
