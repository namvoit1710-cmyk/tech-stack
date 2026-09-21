from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer1_domain.entities.agent_runtime_config import AgentRuntimeConfig
from agent_sdk.layer1_domain.exceptions import RegistrationError
from agent_sdk.layer4_frameworks.registry.http_agent_registry import HttpAgentRegistry


def _make_registration(**overrides) -> AgentRegistration:
    registration = AgentRegistration(
        agent_type="planner",
        version="1.2.3",
        sdk_version="2.0.0",
        domain="workflow",
        endpoint_url="http://planner:8000",
        kind="technical",
        is_published=False,
        attached_agent_ids=["child-agent"],
        tool_ids=["tool-123", "tool-456"],
        agent_runtime_config=AgentRuntimeConfig(system_prompt="Plan work"),
        metadata={
            "description": "Planner agent",
            "routing": {
                "input_schema": {
                    "type": "object",
                    "properties": {"task": {"type": "string"}},
                }
            },
        },
    )
    for key, value in overrides.items():
        setattr(registration, key, value)
    return registration


def test_http_agent_registry_uses_connection_pool_limits():
    with patch(
        "agent_sdk.layer4_frameworks.registry.http_agent_registry.httpx.AsyncClient"
    ) as mock_async_client:
        HttpAgentRegistry()

    mock_async_client.assert_called_once_with(
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        timeout=10.0,
    )


@pytest.mark.asyncio
async def test_register_raises_on_dot_in_agent_type():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    registry._client = AsyncMock()

    with pytest.raises(ValueError, match="agent_type must not contain dots"):
        await registry.register(_make_registration(agent_type="my.agent"))


@pytest.mark.asyncio
async def test_register_creates_then_publishes_and_activates_agent_when_absent():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    registration = _make_registration()
    expected_metadata = HttpAgentRegistry._build_metadata(registration)

    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json = MagicMock(return_value={"agents": []})

    create_response = MagicMock()
    create_response.raise_for_status = MagicMock()
    create_response.json = MagicMock(return_value={"id": "agent-123"})

    publish_response = MagicMock()
    publish_response.raise_for_status = MagicMock()

    activate_response = MagicMock()
    activate_response.raise_for_status = MagicMock()

    agent_unpublished_response = MagicMock()
    agent_unpublished_response.raise_for_status = MagicMock()
    agent_unpublished_response.json = MagicMock(
        return_value={
            "id": "agent-123",
            "status": "inactive",
            "is_published": False,
        }
    )
    agent_published_response = MagicMock()
    agent_published_response.raise_for_status = MagicMock()
    agent_published_response.json = MagicMock(
        return_value={
            "id": "agent-123",
            "status": "inactive",
            "is_published": True,
        }
    )
    agent_active_response = MagicMock()
    agent_active_response.raise_for_status = MagicMock()
    agent_active_response.json = MagicMock(
        return_value={
            "id": "agent-123",
            "status": "active",
            "is_published": True,
        }
    )

    mock_client.get = AsyncMock(
        side_effect=[
            list_response,
            agent_unpublished_response,
            agent_published_response,
            agent_active_response,
        ]
    )
    mock_client.post = AsyncMock(
        side_effect=[create_response, publish_response, activate_response]
    )
    registry._client = mock_client

    agent_id = await registry.register(registration)

    assert agent_id == "agent-123"
    assert mock_client.get.call_args_list[0].args[0].endswith("/api/v1/agents/all")

    create_call = mock_client.post.call_args_list[0]
    assert create_call.args[0].endswith("/api/v1/agents/register")
    assert create_call.kwargs["json"] == {
        "name": "planner",
        "description": "Planner agent",
        "kind": "technical",
        "status": "inactive",
        "config_type": "custom",
        "business": "Plan work",
        "tools": ["tool-123", "tool-456"],
        "agents": ["child-agent"],
        "is_published": False,
        "version": "1.2.3",
        "healthcheck_endpoint": "http://planner:8000/health",
        "invoke_endpoint": "http://planner:8000/api/v1/execute",
        "metadata": expected_metadata,
        "max_concurrency": 1,
    }

    assert (
        mock_client.post.call_args_list[1]
        .args[0]
        .endswith("/api/v1/agents/agent-123/publish")
    )
    assert (
        mock_client.post.call_args_list[2]
        .args[0]
        .endswith("/api/v1/agents/agent-123/activate")
    )


@pytest.mark.asyncio
async def test_register_updates_existing_agent_and_attaches_tool_ids():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    registration = _make_registration()
    definition_hash = HttpAgentRegistry._registration_definition_hash(registration)

    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json = MagicMock(
        return_value={
            "agents": [
                {
                    "id": "agent-123",
                    "name": "planner",
                    "version": "1.2.3",
                    "metadata": {
                        "agent_type": "planner",
                        "logical_registry_name": "planner",
                        "definition_hash": definition_hash,
                        "definition_hash_algorithm": "sha256:v1",
                    },
                },
                {"id": "agent-999", "name": "other-agent"},
            ]
        }
    )

    update_response = MagicMock()
    update_response.raise_for_status = MagicMock()
    update_response.json = MagicMock(return_value={"id": "agent-123"})

    by_ids_response = MagicMock()
    by_ids_response.raise_for_status = MagicMock()
    by_ids_response.json = MagicMock(
        return_value={
            "agents": [
                {
                    "id": "agent-123",
                    "name": "planner",
                    "version": "1.2.3",
                    "status": "active",
                    "is_published": True,
                    "metadata": {
                        "agent_type": "planner",
                        "logical_registry_name": "planner",
                        "definition_hash": definition_hash,
                        "definition_hash_algorithm": "sha256:v1",
                    },
                }
            ]
        }
    )

    active_agent_response = MagicMock()
    active_agent_response.raise_for_status = MagicMock()
    active_agent_response.json = MagicMock(
        return_value={
            "id": "agent-123",
            "name": "planner",
            "version": "1.2.3",
            "status": "active",
            "is_published": True,
            "metadata": {
                "agent_type": "planner",
                "logical_registry_name": "planner",
                "definition_hash": definition_hash,
                "definition_hash_algorithm": "sha256:v1",
            },
        }
    )

    mock_client.get = AsyncMock(side_effect=[list_response, active_agent_response])
    mock_client.put = AsyncMock(return_value=update_response)
    mock_client.post = AsyncMock(side_effect=[by_ids_response])
    registry._client = mock_client

    agent_id = await registry.register(registration)

    assert agent_id == "agent-123"
    mock_client.put.assert_awaited_once()
    assert mock_client.put.call_args.args[0].endswith("/api/v1/agents/agent-123")
    assert mock_client.put.call_args.kwargs["json"] == {
        "name": "planner",
        "description": "Planner agent",
        "kind": "technical",
        "tools": ["tool-123", "tool-456"],
        "agents": ["child-agent"],
        "config_type": "custom",
        "business": "Plan work",
        "version": "1.2.3",
        "healthcheck_endpoint": "http://planner:8000/health",
        "invoke_endpoint": "http://planner:8000/api/v1/execute",
    }


@pytest.mark.asyncio
async def test_register_updates_existing_agent_does_not_retry_legacy_bussiness_key():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    registration = _make_registration()
    definition_hash = HttpAgentRegistry._registration_definition_hash(registration)

    list_response = MagicMock()
    list_response.raise_for_status = MagicMock()
    list_response.json = MagicMock(
        return_value={
            "agents": [
                {
                    "id": "agent-123",
                    "name": "planner",
                    "version": "1.2.3",
                    "metadata": {
                        "agent_type": "planner",
                        "logical_registry_name": "planner",
                        "definition_hash": definition_hash,
                        "definition_hash_algorithm": "sha256:v1",
                    },
                },
            ]
        }
    )

    validation_error = httpx.HTTPStatusError(
        "validation failed",
        request=httpx.Request("PUT", "http://registry/api/v1/agents/agent-123"),
        response=httpx.Response(422),
    )

    update_response = MagicMock()
    update_response.raise_for_status = MagicMock(side_effect=validation_error)

    by_ids_response = MagicMock()
    by_ids_response.raise_for_status = MagicMock()
    by_ids_response.json = MagicMock(
        return_value={
            "agents": [
                {
                    "id": "agent-123",
                    "name": "planner",
                    "version": "1.2.3",
                    "metadata": {
                        "agent_type": "planner",
                        "logical_registry_name": "planner",
                        "definition_hash": definition_hash,
                        "definition_hash_algorithm": "sha256:v1",
                    },
                }
            ]
        }
    )

    mock_client.get = AsyncMock(return_value=list_response)
    mock_client.put = AsyncMock(return_value=update_response)
    mock_client.post = AsyncMock(return_value=by_ids_response)
    registry._client = mock_client

    with pytest.raises(RegistrationError, match="validation failed"):
        await registry.register(registration)

    assert mock_client.put.await_count == 1
    assert mock_client.post.await_count == 1
    assert mock_client.post.await_args_list[0].args[0].endswith("/api/v1/agents/by_ids")
    assert mock_client.put.await_args_list[0].kwargs["json"] == {
        "name": "planner",
        "description": "Planner agent",
        "kind": "technical",
        "tools": ["tool-123", "tool-456"],
        "agents": ["child-agent"],
        "config_type": "custom",
        "business": "Plan work",
        "version": "1.2.3",
        "healthcheck_endpoint": "http://planner:8000/health",
        "invoke_endpoint": "http://planner:8000/api/v1/execute",
    }


@pytest.mark.asyncio
async def test_resolve_agent_id_returns_id_on_name_match_from_available_agents():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(
        return_value={
            "agents": [
                {"id": "agent-123", "name": "planner"},
                {"id": "agent-456", "name": "reviewer"},
            ]
        }
    )
    mock_client.get = AsyncMock(return_value=mock_response)
    registry._client = mock_client

    result = await registry.resolve_agent_id("planner")

    assert result == "agent-123"
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/available")


@pytest.mark.asyncio
async def test_resolve_agent_id_falls_back_to_all_agents_when_no_active_match():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()

    active_response = MagicMock()
    active_response.raise_for_status = MagicMock()
    active_response.json = MagicMock(
        return_value={
            "agents": [
                {"id": "agent-999", "name": "reviewer"},
            ]
        }
    )

    all_response = MagicMock()
    all_response.raise_for_status = MagicMock()
    all_response.json = MagicMock(
        return_value={
            "agents": [
                {"id": "agent-123", "name": "planner"},
                {"id": "agent-456", "name": "reviewer"},
            ]
        }
    )

    by_ids_response = MagicMock()
    by_ids_response.raise_for_status = MagicMock()
    by_ids_response.json = MagicMock(return_value={"agents": []})

    mock_client.get = AsyncMock(side_effect=[active_response, all_response])
    mock_client.post = AsyncMock(return_value=by_ids_response)
    registry._client = mock_client

    result = await registry.resolve_agent_id("planner")

    assert result == "agent-123"
    assert mock_client.get.await_count == 2
    assert (
        mock_client.get.await_args_list[0].args[0].endswith("/api/v1/agents/available")
    )
    assert mock_client.get.await_args_list[1].args[0].endswith("/api/v1/agents/all")


@pytest.mark.asyncio
async def test_resolve_agent_id_returns_none_when_no_name_match():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value={"agents": []})
    mock_client.get = AsyncMock(return_value=mock_response)
    registry._client = mock_client

    assert await registry.resolve_agent_id("planner") is None


@pytest.mark.asyncio
async def test_resolve_agent_id_raises_registration_error_on_transport_failure():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    registry._client = mock_client

    with pytest.raises(RegistrationError, match="refused"):
        await registry.resolve_agent_id("planner")


@pytest.mark.asyncio
async def test_heartbeat_checks_agent_existence_with_get_by_id():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    registry._client = mock_client

    await registry.heartbeat("agent-123", "HEALTHY")

    mock_client.get.assert_awaited_once()
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/agent-123")


@pytest.mark.asyncio
async def test_deregister_deletes_api_v1_agent():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    registry._client = mock_client

    await registry.deregister("agent-123")

    mock_client.post.assert_awaited_once()
    assert mock_client.post.call_args.args[0].endswith(
        "/api/v1/agents/agent-123/deactivate"
    )


@pytest.mark.asyncio
async def test_list_capabilities_returns_registry_capabilities():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={
            "agents": [
                {
                    "agent_type": "planner",
                    "name": "planner",
                    "description": "Planner agent",
                    "semantic_intents": ["plan_tasks"],
                    "queue_metadata": {
                        "queue_name": "planner.queue",
                        "request_topic": "planner.request",
                        "reply_topic": "planner.reply",
                    },
                }
            ]
        }
    )
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    capabilities = await registry.list_capabilities()

    assert capabilities == [
        {
            "agent_type": "planner",
            "name": "planner",
            "description": "Planner agent",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {},
            "required_parameters": [],
            "negative_examples": [],
            "timeout_seconds": 300.0,
            "required_confirmation": True,
            "semantic_intents": ["plan_tasks"],
            "queue_metadata": {
                "queue_name": "planner.queue",
                "request_topic": "planner.request",
                "reply_topic": "planner.reply",
            },
        }
    ]
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/available")


@pytest.mark.asyncio
async def test_list_capabilities_derives_capabilities_from_available_agents_payload():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={
            "agents": [
                {
                    "id": "agent-1",
                    "name": "planner",
                    "description": "Plans work",
                    "invoke_endpoint": "http://planner:36000/api/v1/execute",
                    "healthcheck_endpoint": "http://planner:36000/health",
                    "metadata": {
                        "agent_type": "planner",
                        "routing": {
                            "input_schema": {"type": "object", "properties": {}},
                            "output_schema": {"type": "object"},
                            "required_parameters": ["task"],
                            "negative_examples": ["small talk"],
                            "timeout_seconds": 45.0,
                            "required_confirmation": False,
                        },
                        "semantic_intents": ["plan_tasks"],
                        "queue_metadata": {
                            "queue_name": "planner.queue",
                            "request_topic": "planner.request",
                            "reply_topic": "planner.reply",
                            "delivery_hints": {"mode": "queue"},
                        },
                    },
                }
            ]
        }
    )
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    capabilities = await registry.list_capabilities()

    assert capabilities == [
        {
            "agent_type": "planner",
            "name": "planner",
            "description": "Plans work",
            "input_schema": {"type": "object", "properties": {}},
            "output_schema": {"type": "object"},
            "required_parameters": ["task"],
            "negative_examples": ["small talk"],
            "timeout_seconds": 45.0,
            "required_confirmation": False,
            "semantic_intents": ["plan_tasks"],
            "queue_metadata": {
                "queue_name": "planner.queue",
                "request_topic": "planner.request",
                "reply_topic": "planner.reply",
                "delivery_hints": {"mode": "queue"},
            },
        }
    ]
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/available")


@pytest.mark.asyncio
async def test_get_queue_metadata_returns_registry_queue_payload():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={
            "agents": [
                {
                    "name": "planner",
                    "queue_metadata": {
                        "queue_name": "planner.queue",
                        "request_topic": "planner.request",
                        "reply_topic": "planner.reply",
                    },
                }
            ]
        }
    )
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    queue_metadata = await registry.get_queue_metadata("planner.queue")

    assert queue_metadata["queue_name"] == "planner.queue"
    assert queue_metadata["reply_topic"] == "planner.reply"
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/available")


@pytest.mark.asyncio
async def test_get_queue_metadata_derives_queue_data_from_available_agents_payload():
    registry = HttpAgentRegistry.__new__(HttpAgentRegistry)
    registry.base_url = "http://registry"
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(
        return_value={
            "agents": [
                {
                    "name": "planner",
                    "metadata": {
                        "queue_metadata": {
                            "queue_name": "planner.queue",
                            "request_topic": "planner.request",
                            "reply_topic": "planner.reply",
                            "delivery_hints": {"mode": "queue"},
                        }
                    },
                }
            ]
        }
    )
    mock_client.get = AsyncMock(return_value=mock_resp)
    registry._client = mock_client

    queue_metadata = await registry.get_queue_metadata("planner.queue")

    assert queue_metadata == {
        "queue_name": "planner.queue",
        "request_topic": "planner.request",
        "reply_topic": "planner.reply",
        "delivery_hints": {"mode": "queue"},
    }
    assert mock_client.get.call_args.args[0].endswith("/api/v1/agents/available")


def test_iagentregistry_protocol_has_resolve_agent_id():
    import inspect

    from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry

    assert hasattr(IAgentRegistry, "resolve_agent_id")
    sig = inspect.signature(getattr(IAgentRegistry, "resolve_agent_id"))
    assert "agent_type" in list(sig.parameters.keys())


def test_iagentregistry_protocol_has_registry_capability_helpers():
    from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry

    assert hasattr(IAgentRegistry, "list_capabilities")
    assert hasattr(IAgentRegistry, "get_queue_metadata")


def test_build_runtime_create_payload_includes_registry_create_supported_fields():
    registration = _make_registration(
        agent_runtime_config=AgentRuntimeConfig(
            system_prompt="Plan work",
            llm_model="gpt-4o-mini",
            llm_provider="openai",
            llm_temperature=0.2,
            llm_max_tokens=1024,
            llm_timeout=30.0,
            max_concurrency=4,
            llm_max_retries=3,
        )
    )

    payload = HttpAgentRegistry._build_runtime_create_payload(registration)

    assert payload == {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.2,
        "max_tokens": 1024,
        "timeout_ms": 30000,
        "max_concurrency": 4,
        "retry_count": 3,
    }


def test_build_runtime_update_payload_only_includes_registry_update_supported_fields():
    registration = _make_registration(
        agent_runtime_config=AgentRuntimeConfig(
            system_prompt="Plan work",
            llm_model="gpt-4o-mini",
            llm_provider="openai",
            llm_temperature=0.2,
            llm_max_tokens=1024,
            llm_timeout=30.0,
            max_concurrency=4,
            llm_max_retries=3,
        )
    )

    payload = HttpAgentRegistry._build_runtime_update_payload(registration)

    assert payload == {
        "model": "gpt-4o-mini",
        "provider": "openai",
        "temperature": 0.2,
    }

    assert "max_tokens" not in payload
    assert "timeout_ms" not in payload
    assert "max_concurrency" not in payload
    assert "retry_count" not in payload
