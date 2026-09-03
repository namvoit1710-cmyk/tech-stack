from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer4_frameworks.registry import http_agent_registry as registry_module
from agent_sdk.layer4_frameworks.registry.http_agent_registry import HttpAgentRegistry


def _response(
    payload: dict | list | None = None,
    *,
    status_code: int = 200,
    method: str = "GET",
    url: str = "http://registry.local/api/v1/agents/agent-1",
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={} if payload is None else payload,
        request=httpx.Request(method, url),
    )


class _FakeRegistryClient:
    def __init__(
        self, get_payloads: list[dict], activate_statuses: list[int] | None = None
    ) -> None:
        self.get_payloads = list(get_payloads)
        self.activate_statuses = list(activate_statuses or [200])
        self.posts: list[str] = []

    async def get(self, url: str, timeout: float | None = None) -> httpx.Response:
        assert self.get_payloads, f"unexpected GET {url}"
        return _response(self.get_payloads.pop(0), method="GET", url=url)

    async def post(
        self,
        url: str,
        json: object | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        self.posts.append(url.rsplit("/", 1)[-1])
        status_code = 200
        if url.endswith("/activate"):
            status_code = self.activate_statuses.pop(0)
        return _response({}, status_code=status_code, method="POST", url=url)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_heartbeat_respects_admin_deactivated_agent() -> None:
    client = _FakeRegistryClient(
        [
            {"id": "agent-1", "status": "inactive", "is_published": True},
        ]
    )
    registry = HttpAgentRegistry()
    registry.base_url = "http://registry.local"
    registry._client = client

    await registry.heartbeat("agent-1", "HEALTHY")

    assert client.posts == []


@pytest.mark.asyncio
async def test_heartbeat_respects_admin_unpublished_agent() -> None:
    client = _FakeRegistryClient(
        [
            {"id": "agent-1", "status": "inactive", "is_published": False},
        ]
    )
    registry = HttpAgentRegistry()
    registry.base_url = "http://registry.local"
    registry._client = client

    await registry.heartbeat("agent-1", "HEALTHY")

    assert client.posts == []


@pytest.mark.asyncio
async def test_registration_recovery_publishes_and_activates_inactive_startup_row() -> (
    None
):
    client = _FakeRegistryClient(
        [
            {"id": "agent-1", "status": "inactive", "is_published": False},
            {"id": "agent-1", "status": "inactive", "is_published": True},
            {"id": "agent-1", "status": "active", "is_published": True},
        ]
    )
    registry = HttpAgentRegistry()
    registry.base_url = "http://registry.local"
    registry._client = client

    agent = await registry._ensure_agent_published_and_active("agent-1")

    assert agent["status"] == "active"
    assert agent["is_published"] is True
    assert client.posts == ["publish", "activate"]


@pytest.mark.asyncio
async def test_publish_activate_retries_before_failing_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep = AsyncMock()
    monkeypatch.setattr(registry_module.asyncio, "sleep", sleep)
    client = _FakeRegistryClient(
        [
            {"id": "agent-1", "status": "inactive", "is_published": False},
            {"id": "agent-1", "status": "inactive", "is_published": True},
            {"id": "agent-1", "status": "inactive", "is_published": True},
            {"id": "agent-1", "status": "active", "is_published": True},
        ],
        activate_statuses=[500, 200],
    )
    registry = HttpAgentRegistry()
    registry.base_url = "http://registry.local"
    registry._client = client

    agent = await registry._ensure_agent_published_and_active("agent-1")

    assert agent["status"] == "active"
    assert client.posts == ["publish", "activate", "activate"]
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_register_deactivates_stale_versions_only_after_new_agent_is_confirmed_active() -> (
    None
):
    registration = AgentRegistration(
        agent_type="echo-agent",
        version="1.0",
        sdk_version="test",
        domain="test",
        endpoint_url="http://agent.local",
        kind="technical",
    )
    definition_hash = HttpAgentRegistry._registration_definition_hash(registration)
    existing_agents = {
        "echo-agent": {
            "id": "new-id",
            "name": "echo-agent",
            "version": "1.0",
            "status": "inactive",
            "is_published": False,
            "metadata": {
                "logical_registry_name": "echo-agent",
                "agent_type": "echo-agent",
                "definition_hash": definition_hash,
            },
        },
        "echo-agent__0.90": {
            "id": "old-id",
            "name": "echo-agent__0.90",
            "version": "0.90",
            "status": "active",
            "is_published": True,
            "metadata": {
                "logical_registry_name": "echo-agent",
                "agent_type": "echo-agent",
            },
        },
    }
    events: list[str] = []
    registry = HttpAgentRegistry()
    registry._list_existing_agents = AsyncMock(return_value=existing_agents)

    async def _update_agent(**kwargs):
        events.append("update")
        return _response({})

    async def _ensure_active(agent_id: str) -> dict:
        events.append("ensure-active")
        return {
            "id": agent_id,
            "name": "echo-agent",
            "version": "1.0",
            "status": "active",
            "is_published": True,
            "metadata": existing_agents["echo-agent"]["metadata"],
        }

    async def _deactivate_stale_versions(**kwargs) -> None:
        events.append("deactivate-stale")

    registry._update_agent = _update_agent
    registry._ensure_agent_published_and_active = _ensure_active
    registry._deactivate_stale_versions = _deactivate_stale_versions

    assert await registry.register(registration) == "new-id"
    assert events == ["update", "ensure-active", "deactivate-stale"]
