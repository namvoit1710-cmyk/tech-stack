import httpx
import pytest

from app.layer1_domain.entities.step import ExecutionTarget
from app.layer4_frameworks.registry.http_capability_resolver import (
    HttpCapabilityResolver,
)


class _StubResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://registry.test")
            response = httpx.Response(
                self.status_code,
                request=request,
                json=self._payload if isinstance(self._payload, dict | list) else None,
            )
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=request,
                response=response,
            )


class _StubClient:
    def __init__(self, *, get_map=None, post_map=None):
        self._get_map = get_map or {}
        self._post_map = post_map or {}

    async def get(self, url, **kwargs):
        return self._get_map[url]

    async def post(self, url, **kwargs):
        key = (url, tuple(kwargs.get("json") or []))
        return self._post_map[key]

    async def aclose(self):
        return None


def _build_resolver(get_map=None, post_map=None):
    resolver = HttpCapabilityResolver("http://registry.test")
    resolver._client = _StubClient(get_map=get_map, post_map=post_map)
    return resolver


@pytest.mark.asyncio
async def test_resolve_technical_agent_by_id():
    agent_id = "11111111-1111-4111-8111-111111111111"
    execute_by = f"agent.{agent_id}"
    resolver = _build_resolver(
        get_map={
            f"http://registry.test/api/v1/agents/{agent_id}": _StubResponse(
                200,
                {
                    "id": agent_id,
                    "name": "file-agent",
                    "kind": "technical",
                    "status": "active",
                    "is_published": True,
                    "is_alive": True,
                    "description": "Echoes the user message back.",
                    "version": "0.1.0",
                    "config_type": "custom",
                    "capabilities": [],
                    "agents": [],
                    "workflows": [],
                    "tools": [],
                    "knowledge_base": [],
                    "business": "Echoes the user message back.",
                    "created_at": "2026-06-15T03:17:47.697150",
                    "updated_at": "2026-06-10T11:13:48.222118",
                    "metadata": {
                        "description": "Echoes the user message back.",
                        "agent_type": "echo-agent",
                        "sdk_version": "1.0.0",
                        "agent_domain": "technical",
                        "endpoint_url": "http://ggpnhj6l-3636.asse.devtunnels.ms:3636",
                        "sdk_kind": "SERVICE",
                        "routing": {
                            "negative_examples": [
                                "user needs actual work done, not just echoing"
                            ],
                            "timeout_seconds": 30,
                        },
                        "agent_runtime_config": {
                            "system_prompt": "",
                            "max_concurrency": 1,
                            "llm_model": "gpt-5.4-nano",
                            "llm_provider": "",
                            "llm_temperature": 0.01,
                            "llm_timeout": None,
                            "llm_max_tokens": None,
                            "llm_max_retries": 6,
                        },
                        "queue_metadata": {
                            "queue_name": "",
                            "request_topic": "executor.request.echo",
                            "reply_topic": "agent.reply.executor",
                            "delivery_hints": {},
                            "request_message_type": "executor.request.agent",
                            "response_message_type": "agent.step.status",
                            "request_topics": ["executor.request.echo"],
                            "queue_names": [],
                        },
                        "logical_registry_name": "echo-agent",
                        "definition_hash": "bbf849ceff5d72da6ea1714392c6fc372ce93ab20dacc835333dec4f0a941a1f",
                        "definition_hash_algorithm": "sha256:v1",
                    },
                },
            )
        },
    )

    capability = await resolver.resolve(execute_by)

    assert capability is not None
    assert capability.capability_id == agent_id
    assert capability.execution_target == ExecutionTarget.TECHNICAL_AGENT
    assert capability.agent_type == "echo-agent"
    assert capability.enabled is True
    assert capability.queue_metadata == {
        "queue_name": "",
        "request_topic": "executor.request.echo",
        "reply_topic": "agent.reply.executor",
        "delivery_hints": {},
        "request_message_type": "executor.request.agent",
        "response_message_type": "agent.step.status",
        "request_topics": ["executor.request.echo"],
        "queue_names": [],
    }


@pytest.mark.asyncio
async def test_resolve_tool_by_id_after_agent_404():
    tool_id = "22222222-2222-4222-8222-222222222222"
    execute_by = f"tool.{tool_id}"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                {
                    "tools": [
                        {
                            "id": tool_id,
                            "name": "echo",
                            "status": "active",
                        }
                    ],
                },
            )
        },
    )

    capability = await resolver.resolve(execute_by)

    assert capability is not None
    assert capability.capability_id == tool_id
    assert capability.execution_target == ExecutionTarget.LOCAL_TOOL
    assert capability.tool_name == "echo"
    assert capability.enabled is True


@pytest.mark.asyncio
async def test_resolve_tool_prefers_metadata_tool_name_over_registry_name():
    tool_id = "b85063e7-32c8-46c9-90e2-0c836b1d6fa8"
    execute_by = f"tool.{tool_id}"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                {
                    "tools": [
                        {
                            "id": tool_id,
                            "name": "agent__executor-runtime__echo__0.1.0",
                            "status": "active",
                            "metadata": {"tool_name": "echo"},
                        }
                    ],
                },
            )
        },
    )

    capability = await resolver.resolve(execute_by)

    assert capability is not None
    assert capability.execution_target == ExecutionTarget.LOCAL_TOOL
    assert capability.tool_name == "echo"
    assert capability.enabled is True


@pytest.mark.asyncio
async def test_resolve_disabled_technical_agent_when_unpublished():
    agent_id = "33333333-3333-4333-8333-333333333333"
    execute_by = f"agent.{agent_id}"
    resolver = _build_resolver(
        get_map={
            f"http://registry.test/api/v1/agents/{agent_id}": _StubResponse(
                200,
                {
                    "id": agent_id,
                    "name": "dead-agent",
                    "kind": "technical",
                    "status": "inactive",
                    "is_published": False,
                    "is_alive": False,
                },
            )
        },
    )

    capability = await resolver.resolve(execute_by)

    assert capability is not None
    assert capability.execution_target == ExecutionTarget.TECHNICAL_AGENT
    assert capability.enabled is False


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unknown_registry_id():
    capability_id = "missing-id"
    execute_by = f"tool.{capability_id}"
    resolver = _build_resolver(
        post_map={
            (
                "http://registry.test/api/v1/tools/by_ids",
                (capability_id,),
            ): _StubResponse(404, {"detail": "not found"})
        },
    )

    capability = await resolver.resolve(execute_by)

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_tool_without_name():
    tool_id = "tool-789"
    execute_by = f"tool.{tool_id}"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                {"tools": [{"id": tool_id, "status": "active"}]},
            )
        },
    )

    capability = await resolver.resolve(execute_by)

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_malformed_tool_metadata():
    tool_id = "99999999-9999-4999-8999-999999999999"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                {
                    "tools": [
                        {
                            "id": tool_id,
                            "name": "echo",
                            "status": "active",
                            "metadata": {"tool_name": 123},
                        }
                    ]
                },
            )
        },
    )

    capability = await resolver.resolve(f"tool.{tool_id}")

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_returns_none_without_registry_url():
    resolver = HttpCapabilityResolver("")
    capability = await resolver.resolve("tool.tool-123")
    await resolver.close()

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_invalid_execute_by_prefix():
    resolver = _build_resolver()

    capability = await resolver.resolve("tool-123")

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_uses_cache_on_second_lookup():
    tool_id = "44444444-4444-4444-8444-444444444444"
    execute_by = f"tool.{tool_id}"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                {
                    "tools": [
                        {
                            "id": tool_id,
                            "name": "echo",
                            "status": "active",
                        }
                    ],
                },
            )
        },
    )

    first = await resolver.resolve(execute_by)
    second = await resolver.resolve(execute_by)

    assert first is not None
    assert second is first


@pytest.mark.asyncio
async def test_resolve_returns_none_for_malformed_agent_payload():
    agent_id = "55555555-5555-4555-8555-555555555555"
    resolver = _build_resolver(
        get_map={
            f"http://registry.test/api/v1/agents/{agent_id}": _StubResponse(
                200,
                {"name": "bad-agent"},
            )
        },
    )

    capability = await resolver.resolve(f"agent.{agent_id}")

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_uses_configuration_queue_metadata_fallback():
    agent_id = "66666666-6666-4666-8666-666666666666"
    resolver = _build_resolver(
        get_map={
            f"http://registry.test/api/v1/agents/{agent_id}": _StubResponse(
                200,
                {
                    "id": agent_id,
                    "name": "file-agent",
                    "kind": "technical",
                    "status": "active",
                    "is_published": True,
                    "is_alive": True,
                    "configuration": {
                        "queue_metadata": {
                            "request_topic": "executor.request.agent",
                            "reply_topic": "agent.step.status",
                        }
                    },
                },
            )
        },
    )

    capability = await resolver.resolve(f"agent.{agent_id}")

    assert capability is not None
    assert capability.queue_metadata == {
        "request_topic": "executor.request.agent",
        "reply_topic": "agent.step.status",
        "delivery_hints": {},
        "request_topics": [],
        "queue_names": [],
    }


@pytest.mark.asyncio
async def test_resolve_returns_none_for_malformed_tools_list():
    tool_id = "77777777-7777-4777-8777-777777777777"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                {"tools": {"id": tool_id}, "total": 1},
            )
        },
    )

    capability = await resolver.resolve(f"tool.{tool_id}")

    assert capability is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_malformed_tools_payload():
    tool_id = "88888888-8888-4888-8888-888888888888"
    resolver = _build_resolver(
        post_map={
            ("http://registry.test/api/v1/tools/by_ids", (tool_id,)): _StubResponse(
                200,
                [{"id": tool_id, "name": "echo", "status": "active"}],
            )
        },
    )

    capability = await resolver.resolve(f"tool.{tool_id}")

    assert capability is None
