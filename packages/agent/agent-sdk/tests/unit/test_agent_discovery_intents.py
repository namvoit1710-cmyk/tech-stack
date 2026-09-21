from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.exceptions import RegistrationError


@pytest.mark.asyncio
async def test_discover_prefers_registry_capabilities_for_intents_and_queue_metadata():
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_capabilities = AsyncMock(
        return_value=[
            {
                "agent_type": "planner",
                "name": "planner",
                "description": "Plans work",
                "semantic_intents": ["plan_tasks", "delegate_work"],
                "queue_metadata": {
                    "queue_name": "planner.queue",
                    "request_topic": "planner.request",
                    "reply_topic": "planner.reply",
                },
                "input_schema": {"type": "object", "properties": {}},
            }
        ]
    )
    registry.list_active_agents = AsyncMock(return_value=[])

    service = AgentDiscoveryService(registry=registry)
    capabilities, tools = await service.discover()

    assert len(capabilities) == 1
    assert tools == []
    assert capabilities[0].agent_type == "planner"
    assert capabilities[0].semantic_intents == ["plan_tasks", "delegate_work"]
    assert capabilities[0].queue_metadata.queue_name == "planner.queue"


@pytest.mark.asyncio
async def test_discover_falls_back_to_active_agents_when_capability_listing_is_empty():
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_capabilities = AsyncMock(return_value=[])
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "worker-1",
                "agent_type": "worker",
                "name": "worker",
                "description": "Does work",
                "routing": {"input_schema": {"type": "object", "properties": {}}},
                "queue_metadata": {
                    "queue_name": "worker.queue",
                    "request_topic": "worker.request",
                    "reply_topic": "worker.reply",
                },
            }
        ]
    )

    service = AgentDiscoveryService(registry=registry)
    capabilities, _ = await service.discover()

    assert len(capabilities) == 1
    assert capabilities[0].agent_type == "worker"
    assert capabilities[0].queue_metadata.queue_name == "worker.queue"


@pytest.mark.asyncio
async def test_discover_falls_back_to_active_agents_when_capability_listing_raises_registration_error():
    from agent_sdk.layer2_application.services.agent_discovery_service import (
        AgentDiscoveryService,
    )

    registry = MagicMock()
    registry.list_capabilities = AsyncMock(side_effect=RegistrationError("404"))
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "id": "planner-1",
                "name": "planner",
                "description": "Plans work",
                "metadata": {
                    "agent_type": "planner",
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
    assert capabilities[0].queue_metadata.queue_name == "planner.queue"
