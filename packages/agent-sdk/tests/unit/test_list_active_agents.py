from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_list_active_agents_returns_available_agent_payload():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    list_response = MagicMock()
    list_response.json.return_value = {
        "agents": [
            {
                "id": "uuid-1",
                "name": "agent-a",
                "description": "Agent A does X",
                "invoke_endpoint": "http://agent-a:8000/api/v1/execute",
                "metadata": {"agent_type": "agent-a", "agent_domain": "test"},
            },
            {
                "id": "uuid-2",
                "name": "agent-b",
                "description": "Agent B does Y",
                "invoke_endpoint": "http://agent-b:8000/api/v1/execute",
                "metadata": {"agent_type": "agent-b", "agent_domain": "test"},
            },
        ],
        "total": 2,
    }
    list_response.raise_for_status = MagicMock()

    with patch(
        "agent_sdk.layer4_frameworks.registry.http_agent_registry.settings"
    ) as mock_settings:
        mock_settings.REGISTRY_URL = "http://localhost:8080"
        registry = HttpAgentRegistry()
        registry._client = MagicMock()
        registry._client.get = AsyncMock(return_value=list_response)

        agents = await registry.list_active_agents()

    assert len(agents) == 2
    assert agents[0]["id"] == "uuid-1"
    assert agents[0]["metadata"]["agent_type"] == "agent-a"
    assert registry._client.get.call_args.args[0].endswith("/api/v1/agents/available")


@pytest.mark.asyncio
async def test_list_active_agents_empty_registry():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    list_response = MagicMock()
    list_response.json.return_value = {"agents": [], "total": 0}
    list_response.raise_for_status = MagicMock()

    with patch(
        "agent_sdk.layer4_frameworks.registry.http_agent_registry.settings"
    ) as mock_settings:
        mock_settings.REGISTRY_URL = "http://localhost:8080"
        registry = HttpAgentRegistry()
        registry._client = MagicMock()
        registry._client.get = AsyncMock(return_value=list_response)

        agents = await registry.list_active_agents()

    assert agents == []


@pytest.mark.asyncio
async def test_list_active_agents_filters_by_domain_client_side():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    list_response = MagicMock()
    list_response.json.return_value = {
        "agents": [
            {"id": "uuid-1", "name": "agent-a", "domain": "finance"},
            {"id": "uuid-2", "name": "agent-b", "domain": "ops"},
        ],
        "total": 2,
    }
    list_response.raise_for_status = MagicMock()

    with patch(
        "agent_sdk.layer4_frameworks.registry.http_agent_registry.settings"
    ) as mock_settings:
        mock_settings.REGISTRY_URL = "http://localhost:8080"
        registry = HttpAgentRegistry()
        registry._client = MagicMock()
        registry._client.get = AsyncMock(return_value=list_response)

        agents = await registry.list_active_agents(domain="finance")

    assert agents == [{"id": "uuid-1", "name": "agent-a", "domain": "finance"}]


@pytest.mark.asyncio
async def test_list_active_agents_handles_missing_agents_key():
    from agent_sdk.layer4_frameworks.registry.http_agent_registry import (
        HttpAgentRegistry,
    )

    list_response = MagicMock()
    list_response.json.return_value = {"items": [], "total": 0}
    list_response.raise_for_status = MagicMock()

    with patch(
        "agent_sdk.layer4_frameworks.registry.http_agent_registry.settings"
    ) as mock_settings:
        mock_settings.REGISTRY_URL = "http://localhost:8080"
        registry = HttpAgentRegistry()
        registry._client = MagicMock()
        registry._client.get = AsyncMock(return_value=list_response)

        agents = await registry.list_active_agents()

    assert agents == []
