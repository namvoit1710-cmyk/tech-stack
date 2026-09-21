from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_registry_endpoint_resolver_prefers_configuration_endpoint_url():
    from agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver import (
        RegistryEndpointResolver,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "agent-1",
                "configuration": {"endpoint_url": "http://agent-1:8000"},
            }
        ]
    )

    resolver = RegistryEndpointResolver(registry)

    assert await resolver.resolve_endpoint("agent-1") == "http://agent-1:8000"


@pytest.mark.asyncio
async def test_registry_endpoint_resolver_falls_back_to_health_endpoint():
    from agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver import (
        RegistryEndpointResolver,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "agent_id": "agent-2",
                "health_endpoint": "http://agent-2:8000/health",
            }
        ]
    )

    resolver = RegistryEndpointResolver(registry)

    assert await resolver.resolve_endpoint("agent-2") == "http://agent-2:8000"


@pytest.mark.asyncio
async def test_registry_endpoint_resolver_raises_for_missing_agent():
    from agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver import (
        RegistryEndpointResolver,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(return_value=[])
    resolver = RegistryEndpointResolver(registry)

    with pytest.raises(ValueError, match="No active agent found"):
        await resolver.resolve_endpoint("missing-agent")


@pytest.mark.asyncio
async def test_registry_endpoint_resolver_prefers_invoke_endpoint_for_current_registry_shape():
    from agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver import (
        RegistryEndpointResolver,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "id": "agent-3",
                "invoke_endpoint": "http://agent-3:8000/api/v1/execute",
            }
        ]
    )

    resolver = RegistryEndpointResolver(registry)

    assert (
        await resolver.resolve_endpoint("agent-3")
        == "http://agent-3:8000/api/v1/execute"
    )


@pytest.mark.asyncio
async def test_registry_endpoint_resolver_falls_back_to_healthcheck_endpoint():
    from agent_sdk.layer4_frameworks.registry.registry_endpoint_resolver import (
        RegistryEndpointResolver,
    )

    registry = MagicMock()
    registry.list_active_agents = AsyncMock(
        return_value=[
            {
                "id": "agent-4",
                "healthcheck_endpoint": "http://agent-4:8000/health",
            }
        ]
    )

    resolver = RegistryEndpointResolver(registry)

    assert await resolver.resolve_endpoint("agent-4") == "http://agent-4:8000"
