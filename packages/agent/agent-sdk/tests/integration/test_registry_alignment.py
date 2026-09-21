import os
from contextlib import suppress
from uuid import uuid4

import httpx
import pytest

from agent_sdk import ToolRegistrar, tool
from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer1_domain.entities.agent_runtime_config import AgentRuntimeConfig
from agent_sdk.layer4_frameworks.config.app_config import settings as sdk_settings
from agent_sdk.layer4_frameworks.registry import HttpAgentRegistry, HttpToolRegistry

REGISTRY_URL = os.getenv("REGISTRY_URL", "http://127.0.0.1:8000").rstrip("/")


@tool
def registry_alignment_probe_tool(text: str) -> str:
    """Return the input text for live registry-alignment coverage."""
    return text


@pytest.mark.asyncio
async def test_sdk_registers_agent_and_executor_tool_against_live_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sdk_settings, "REGISTRY_URL", REGISTRY_URL)

    unique_suffix = uuid4().hex
    owner_name = f"executor-runtime-{unique_suffix}"
    owner_version = "1.0.0"
    agent_type = f"registry-probe-{unique_suffix}"
    endpoint_url = "http://127.0.0.1:36000"
    system_prompt = f"Registry alignment probe {unique_suffix}"
    registry_tool_name = f"executor_service__{owner_name}__registry_alignment_probe_tool__{owner_version}"

    tool_registry = HttpToolRegistry()
    agent_registry = HttpAgentRegistry()
    registry_client = httpx.AsyncClient(base_url=REGISTRY_URL, timeout=10.0)
    agent_id: str | None = None

    try:
        try:
            health_response = await registry_client.get("/health")
        except httpx.ConnectError as exc:
            pytest.skip(f"Live registry unavailable at {REGISTRY_URL}: {exc}")
        health_response.raise_for_status()

        tool_ids = await ToolRegistrar(tool_registry).register_tools(
            tools=[registry_alignment_probe_tool],
            owner_kind="executor_service",
            owner_name=owner_name,
            owner_version=owner_version,
        )
        tool_id = tool_ids["registry_alignment_probe_tool"]

        agent_id = await agent_registry.register(
            AgentRegistration(
                agent_type=agent_type,
                version=owner_version,
                sdk_version=sdk_settings.SDK_VERSION,
                domain="workflow",
                endpoint_url=endpoint_url,
                kind="technical",
                tool_ids=[tool_id],
                agent_runtime_config=AgentRuntimeConfig(system_prompt=system_prompt),
                metadata={
                    "description": f"Live registry alignment probe {unique_suffix}"
                },
            )
        )

        tools_response = await registry_client.get("/api/v1/tools/all")
        tools_response.raise_for_status()
        tools_payload = tools_response.json()
        tools = (
            tools_payload if isinstance(tools_payload, list) else tools_payload["tools"]
        )
        registered_tool = next(tool for tool in tools if tool["id"] == tool_id)
        assert registered_tool["name"] == registry_tool_name

        agents_response = await registry_client.get("/api/v1/agents/all")
        agents_response.raise_for_status()
        agents_payload = agents_response.json()
        agents = (
            agents_payload
            if isinstance(agents_payload, list)
            else agents_payload["agents"]
        )
        registered_agent = next(agent for agent in agents if agent["id"] == agent_id)
        assert registered_agent["name"] == agent_type

        agent_detail_response = await registry_client.get(f"/api/v1/agents/{agent_id}")
        agent_detail_response.raise_for_status()
        agent_detail = agent_detail_response.json()
        assert tool_id in agent_detail["tools"]
        assert agent_detail["business"] == system_prompt
        assert agent_detail["healthcheck_endpoint"] == f"{endpoint_url}/health"
        assert agent_detail["invoke_endpoint"] == f"{endpoint_url}/api/v1/execute"
    finally:
        if agent_id is not None:
            with suppress(Exception):
                await agent_registry.deregister(agent_id)
        await registry_client.aclose()
        await agent_registry.close()
        await tool_registry.close()
