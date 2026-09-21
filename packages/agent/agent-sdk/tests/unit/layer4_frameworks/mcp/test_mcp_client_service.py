"""Tests for MCPClientService."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.tools import BaseTool
from pydantic import BaseModel

import agent_sdk.layer4_frameworks.mcp.mcp_client_service as svc_module
from agent_sdk.layer4_frameworks.mcp.mcp_client_service import MCPClientService


def _make_raw_mcp_tool(
    name: str = "add",
    description: str = "Add two numbers",
    input_schema: dict | None = None,
):
    return SimpleNamespace(
        name=name,
        description=description,
        inputSchema=(
            {
                "type": "object",
                "properties": {
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                },
                "required": ["x", "y"],
            }
            if input_schema is None
            else input_schema
        ),
        annotations=None,
        meta=None,
    )


def _make_call_tool_result(
    text: str = '{"sum": 3}',
    structured_content: dict | None = None,
    is_error: bool = False,
):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        structuredContent=structured_content,
        isError=is_error,
    )


def _normalize_transport(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def _build_endpoint(
    server_url: str,
    transport: str,
    sse_path: str,
    streamable_http_path: str,
) -> str:
    path = (
        sse_path if _normalize_transport(transport) == "sse" else streamable_http_path
    )
    return f"{server_url.rstrip('/')}{path}"


def _install_shared_client_module(
    monkeypatch,
    *,
    raw_tools: list[object],
    call_result: object | None = None,
    call_side_effect: Exception | None = None,
    list_tools_side_effect: object | None = None,
    open_mcp_session_impl: object | None = None,
    fake_session: object | None = None,
):
    state: dict[str, Any] = {
        "configs": [],
        "sessions_entered": 0,
        "sessions_exited": 0,
    }
    fake_session = fake_session if fake_session is not None else object()

    def build_config(**kwargs):
        transport = kwargs.get("transport", "streamable-http")
        sse_path = kwargs.get("sse_path", "/mcp/sse/sse")
        streamable_http_path = kwargs.get("streamable_http_path", "/mcp/http/mcp")
        config = SimpleNamespace(
            server_url=kwargs["server_url"],
            transport=transport,
            sse_path=sse_path,
            streamable_http_path=streamable_http_path,
            endpoint=_build_endpoint(
                kwargs["server_url"],
                transport,
                sse_path,
                streamable_http_path,
            ),
        )
        state["configs"].append(config)
        return config

    @asynccontextmanager
    async def fake_open_mcp_session(config):
        state["sessions_entered"] += 1
        try:
            yield fake_session
        finally:
            state["sessions_exited"] += 1

    shared_client_module = SimpleNamespace(
        MCPClientConfig=MagicMock(side_effect=build_config),
        normalize_transport=_normalize_transport,
        open_mcp_session=open_mcp_session_impl or fake_open_mcp_session,
        list_tools=AsyncMock(),
        call_tool=AsyncMock(),
    )
    if list_tools_side_effect is None:
        shared_client_module.list_tools.return_value = raw_tools
    else:
        shared_client_module.list_tools.side_effect = list_tools_side_effect

    if call_side_effect is None:
        shared_client_module.call_tool.return_value = (
            call_result if call_result is not None else _make_call_tool_result()
        )
    else:
        shared_client_module.call_tool.side_effect = call_side_effect

    monkeypatch.setattr(
        svc_module,
        "shared_mcp_client_module",
        shared_client_module,
        raising=False,
    )
    return shared_client_module, state, fake_session


def _tool_schema(tool) -> dict:
    args_schema = tool.args_schema
    if isinstance(args_schema, dict):
        return args_schema
    if hasattr(args_schema, "model_json_schema"):
        return args_schema.model_json_schema()
    return {}


class _TrackedSessionManager:
    def __init__(self, session: object, enter_gate: asyncio.Event | None = None):
        self._session = session
        self._enter_gate = enter_gate
        self.enter_calls = 0
        self.exit_calls = 0

    async def __aenter__(self):
        self.enter_calls += 1
        if self._enter_gate is not None:
            await self._enter_gate.wait()
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_calls += 1


@pytest.fixture
def empty_service():
    """Service with no MCP servers configured."""
    return MCPClientService(server_configs=[], logger=MagicMock())


@pytest.fixture
def configured_service():
    """Service with one MCP server configured."""
    return MCPClientService(
        server_configs=[{"name": "math", "url": "http://localhost:8080/mcp"}],
        logger=MagicMock(),
    )


@pytest.mark.asyncio
async def test_get_tools_no_servers(empty_service):
    """get_tools() returns empty list when no servers are configured."""
    tools = await empty_service.get_tools()
    assert tools == []


@pytest.mark.asyncio
async def test_legacy_endpoint_url_is_used_as_exact_endpoint(
    configured_service, monkeypatch
):
    """Legacy MCP server URLs should be preserved as exact endpoints."""
    shared_client_module, state, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
    )

    await configured_service.get_tools()

    shared_client_module.MCPClientConfig.assert_called_once()
    assert state["configs"][0].endpoint == "http://localhost:8080/mcp"
    assert state["configs"][0].transport == "streamable-http"


@pytest.mark.asyncio
async def test_explicit_transport_and_paths_override_defaults(monkeypatch):
    """Explicit shared-client transport settings should override the defaults."""
    service = MCPClientService(
        server_configs=[
            {
                "name": "math",
                "server_url": "http://localhost:8080",
                "transport": "SSE",
                "sse_path": "/custom/sse",
                "streamable_http_path": "/custom/http",
            }
        ],
        logger=MagicMock(),
    )
    shared_client_module, state, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
    )

    await service.get_tools()

    shared_client_module.MCPClientConfig.assert_called_once()
    assert state["configs"][0].transport == "sse"
    assert state["configs"][0].endpoint == "http://localhost:8080/custom/sse"


@pytest.mark.asyncio
async def test_get_tools_returns_langchain_basetools_from_shared_client(
    configured_service, monkeypatch
):
    """get_tools() should return LangChain BaseTool wrappers built from the shared MCP client flow."""
    raw_tool = _make_raw_mcp_tool()
    _install_shared_client_module(monkeypatch, raw_tools=[raw_tool])

    tools = await configured_service.get_tools()

    assert len(tools) == 1
    assert isinstance(tools[0], BaseTool)
    assert tools[0].name == "add"
    assert tools[0].description == "Add two numbers"
    assert isinstance(tools[0].args_schema, type)
    assert issubclass(tools[0].args_schema, BaseModel)
    assert _tool_schema(tools[0])["properties"]["x"]["type"] == "number"


@pytest.mark.asyncio
async def test_get_tools_uses_empty_pydantic_model_for_empty_input_schema(
    configured_service, monkeypatch
):
    """Tools without an object input schema should still expose a valid empty Pydantic args model."""
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool(input_schema={})],
    )

    tools = await configured_service.get_tools()

    assert isinstance(tools[0].args_schema, type)
    assert issubclass(tools[0].args_schema, BaseModel)
    assert tools[0].args == {}


@pytest.mark.asyncio
async def test_get_tools_namespaces_colliding_tool_names(monkeypatch):
    """Duplicate MCP tool names should be disambiguated with the server name."""
    service = MCPClientService(
        server_configs=[
            {"name": "alpha", "url": "http://alpha.example/mcp"},
            {"name": "beta", "url": "http://beta.example/mcp"},
        ],
        logger=MagicMock(),
    )
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        list_tools_side_effect=[
            [_make_raw_mcp_tool(name="search_db")],
            [_make_raw_mcp_tool(name="search_db")],
        ],
    )

    tools = await service.get_tools()

    assert [tool.name for tool in tools] == ["alpha_search_db", "beta_search_db"]
    assert tools[0].metadata["mcp_raw_name"] == "search_db"
    assert tools[1].metadata["mcp_raw_name"] == "search_db"


@pytest.mark.asyncio
async def test_get_filtered_tools_matches_raw_names_for_namespaced_duplicates(
    monkeypatch,
):
    """Raw-name filtering should still work when duplicate tools are namespaced."""
    service = MCPClientService(
        server_configs=[
            {"name": "alpha", "url": "http://alpha.example/mcp"},
            {"name": "beta", "url": "http://beta.example/mcp"},
        ],
        logger=MagicMock(),
    )
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        list_tools_side_effect=[
            [_make_raw_mcp_tool(name="search_db")],
            [_make_raw_mcp_tool(name="search_db")],
        ],
    )

    filtered = await service.get_filtered_tools(["search_db"])

    assert [tool.name for tool in filtered] == ["alpha_search_db", "beta_search_db"]


@pytest.mark.asyncio
async def test_get_filtered_tools(configured_service, monkeypatch):
    """get_filtered_tools() returns only tools matching the filter list."""
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[
            _make_raw_mcp_tool(name="add"),
            _make_raw_mcp_tool(name="multiply"),
            _make_raw_mcp_tool(name="divide"),
        ],
    )

    filtered = await configured_service.get_filtered_tools(["add", "divide"])

    names = [tool.name for tool in filtered]
    assert names == ["add", "divide"]


@pytest.mark.asyncio
async def test_get_filtered_tools_empty_filter_returns_all(
    configured_service, monkeypatch
):
    """get_filtered_tools([]) returns all tools (no filtering)."""
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool(name="add"), _make_raw_mcp_tool(name="divide")],
    )

    filtered = await configured_service.get_filtered_tools([])

    assert [tool.name for tool in filtered] == ["add", "divide"]


@pytest.mark.asyncio
async def test_get_tools_returns_empty_when_shared_client_probe_raises(
    configured_service, monkeypatch
):
    """get_tools() returns empty list when shared-client discovery raises."""
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        list_tools_side_effect=RuntimeError("probe failed"),
    )

    tools = await configured_service.get_tools()

    assert tools == []


@pytest.mark.asyncio
async def test_get_tools_retries_after_initial_probe_failure(monkeypatch):
    """get_tools() retries shared-client discovery after a failed initial probe."""
    service = MCPClientService(
        server_configs=[{"name": "math", "url": "http://localhost:8080/mcp"}],
        logger=MagicMock(),
    )
    shared_client_module, _, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        list_tools_side_effect=[RuntimeError("probe failed"), [_make_raw_mcp_tool()]],
    )

    first_result = await service.get_tools()
    second_result = await service.get_tools()

    assert first_result == []
    assert len(second_result) == 1
    assert isinstance(second_result[0], BaseTool)
    assert shared_client_module.list_tools.await_count == 2


@pytest.mark.asyncio
async def test_get_tools_wraps_discovery_in_timeout(configured_service, monkeypatch):
    """Discovery should enforce a timeout around the shared-client list_tools call."""
    recorded_timeouts: list[float] = []
    service_asyncio = cast(Any, svc_module).asyncio
    real_wait_for = service_asyncio.wait_for

    async def tracking_wait_for(awaitable, timeout):
        recorded_timeouts.append(timeout)
        return await real_wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(service_asyncio, "wait_for", tracking_wait_for)
    _install_shared_client_module(monkeypatch, raw_tools=[_make_raw_mcp_tool()])

    await configured_service.get_tools()

    assert recorded_timeouts == [10.0]


@pytest.mark.asyncio
async def test_get_tools_times_out_when_connection_handshake_stalls(monkeypatch):
    """Discovery should time out even when the MCP connection handshake never completes."""
    service = cast(Any, MCPClientService)(
        [{"name": "math", "url": "http://localhost:8080/mcp"}],
        MagicMock(),
        0.01,
    )

    class HangingManager:
        async def __aenter__(self):
            await asyncio.Event().wait()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    shared_client_module = SimpleNamespace(
        MCPClientConfig=MagicMock(
            side_effect=lambda **kwargs: SimpleNamespace(endpoint=kwargs["server_url"])
        ),
        normalize_transport=_normalize_transport,
        open_mcp_session=lambda config: HangingManager(),
        list_tools=AsyncMock(),
        call_tool=AsyncMock(),
    )
    monkeypatch.setattr(
        svc_module,
        "shared_mcp_client_module",
        shared_client_module,
        raising=False,
    )

    tools = await asyncio.wait_for(service.get_tools(), timeout=0.1)

    assert tools == []
    assert getattr(service, "last_discovery_failures") == ["math"]
    assert shared_client_module.list_tools.await_count == 0


@pytest.mark.asyncio
async def test_get_tools_discovers_servers_concurrently(monkeypatch):
    """Discovery should probe configured MCP servers concurrently instead of serially."""
    service = MCPClientService(
        server_configs=[
            {"name": "alpha", "url": "http://alpha.example/mcp"},
            {"name": "beta", "url": "http://beta.example/mcp"},
        ],
        logger=MagicMock(),
    )
    active_calls = 0
    max_concurrency = 0

    @asynccontextmanager
    async def fake_open_mcp_session(config):
        yield config.endpoint

    async def fake_list_tools(session):
        nonlocal active_calls, max_concurrency
        active_calls += 1
        max_concurrency = max(max_concurrency, active_calls)
        await asyncio.sleep(0.01)
        active_calls -= 1
        return [_make_raw_mcp_tool(name=f"tool_{session.rsplit('/', 1)[-1]}")]

    shared_client_module = SimpleNamespace(
        MCPClientConfig=MagicMock(
            side_effect=lambda **kwargs: SimpleNamespace(endpoint=kwargs["server_url"])
        ),
        normalize_transport=_normalize_transport,
        open_mcp_session=fake_open_mcp_session,
        list_tools=AsyncMock(side_effect=fake_list_tools),
        call_tool=AsyncMock(),
    )
    monkeypatch.setattr(
        svc_module,
        "shared_mcp_client_module",
        shared_client_module,
        raising=False,
    )

    tools = await service.get_tools()

    assert len(tools) == 2
    assert max_concurrency == 2


@pytest.mark.asyncio
async def test_wrapped_tool_invocation_uses_shared_client_call_tool(
    configured_service, monkeypatch
):
    """Wrapped MCP tools should invoke the shared client for execution."""
    shared_client_module, state, fake_session = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        call_result=_make_call_tool_result(structured_content={"sum": 3}),
    )

    tools = await configured_service.get_tools()
    await tools[0].ainvoke({"x": 1, "y": 2})

    call_args = shared_client_module.call_tool.await_args
    assert call_args.args[0] is fake_session
    assert call_args.args[1] == "add"
    if len(call_args.args) > 2:
        assert call_args.args[2] == {"x": 1, "y": 2}
    else:
        assert call_args.kwargs == {"arguments": {"x": 1, "y": 2}}
    assert state["sessions_entered"] == 2
    assert state["sessions_exited"] == 1

    await configured_service.close()

    assert state["sessions_exited"] == 2


@pytest.mark.asyncio
async def test_wrapped_tool_returns_content_and_artifact_shape(
    configured_service, monkeypatch
):
    """Wrapped MCP tools should convert results to LangChain content/artifact tuples."""
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        call_result=_make_call_tool_result(
            text='{"sum": 3}',
            structured_content={"sum": 3},
        ),
    )

    tools = await configured_service.get_tools()
    content_only = await tools[0].ainvoke({"x": 1, "y": 2})
    content, artifact = await tools[0].coroutine(x=1, y=2)

    assert isinstance(content_only, list)
    assert content_only[0]["type"] == "text"
    assert content_only[0]["text"] == '{"sum": 3}'
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == '{"sum": 3}'
    assert artifact == {"structured_content": {"sum": 3}}


@pytest.mark.asyncio
async def test_wrapped_tool_returns_tool_error_message_on_mcp_error_response(
    configured_service, monkeypatch
):
    """Wrapped MCP tools should return a handled tool error string when MCP reports an error response."""
    _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        call_result=_make_call_tool_result(text="boom", is_error=True),
    )

    tools = await configured_service.get_tools()

    result = await tools[0].ainvoke({"x": 1, "y": 2})

    assert result == "boom"
    assert tools[0].handle_tool_error is True


@pytest.mark.asyncio
async def test_wrapped_tool_reuses_call_session_between_invocations(
    configured_service, monkeypatch
):
    """Tool invocations should reuse the shared-client session instead of reopening each call."""
    _, state, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        call_result=_make_call_tool_result(structured_content={"sum": 3}),
    )

    tools = await configured_service.get_tools()
    await tools[0].ainvoke({"x": 1, "y": 2})
    await tools[0].ainvoke({"x": 2, "y": 3})

    assert state["sessions_entered"] == 2
    assert state["sessions_exited"] == 1

    await configured_service.close()

    assert state["sessions_exited"] == 2


@pytest.mark.asyncio
async def test_wrapped_tool_allows_concurrent_calls_on_same_session(
    configured_service, monkeypatch
):
    """Concurrent tool calls should reuse one session without serializing the actual call_tool work."""
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_calls = asyncio.Event()
    invocation_count = 0

    async def fake_call_tool(session, tool_name, arguments=None):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            first_started.set()
        else:
            second_started.set()
        await release_calls.wait()
        return _make_call_tool_result(structured_content={"sum": 3})

    shared_client_module, _, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
    )
    shared_client_module.call_tool.side_effect = fake_call_tool

    tools = await configured_service.get_tools()
    first_call = asyncio.create_task(tools[0].ainvoke({"x": 1, "y": 2}))
    await asyncio.wait_for(first_started.wait(), timeout=0.1)

    second_call = asyncio.create_task(tools[0].ainvoke({"x": 2, "y": 3}))
    await asyncio.wait_for(second_started.wait(), timeout=0.1)

    release_calls.set()
    await asyncio.gather(first_call, second_call)


@pytest.mark.asyncio
async def test_get_or_open_call_session_closes_manager_when_enter_raises(
    configured_service, monkeypatch
):
    """Timed out or failed manual session opens should always unwind the manager."""
    manager = _TrackedSessionManager(object())
    shared_client_module = SimpleNamespace(
        open_mcp_session=MagicMock(return_value=manager)
    )
    monkeypatch.setattr(
        svc_module,
        "shared_mcp_client_module",
        shared_client_module,
        raising=False,
    )

    async def failing_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError

    monkeypatch.setattr(cast(Any, svc_module).asyncio, "wait_for", failing_wait_for)

    with pytest.raises(asyncio.TimeoutError):
        await configured_service._get_or_open_call_session(
            "math",
            SimpleNamespace(endpoint="http://localhost:8080/mcp"),
        )

    assert manager.exit_calls == 1


@pytest.mark.asyncio
async def test_close_waits_for_in_flight_call_before_closing_session(
    configured_service, monkeypatch
):
    """close() should wait for in-flight tool calls to finish before tearing down the shared session."""
    call_started = asyncio.Event()
    release_call = asyncio.Event()

    async def fake_call_tool(session, tool_name, arguments=None):
        call_started.set()
        await release_call.wait()
        return _make_call_tool_result(structured_content={"sum": 3})

    shared_client_module, state, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
    )
    shared_client_module.call_tool.side_effect = fake_call_tool

    tools = await configured_service.get_tools()
    in_flight_call = asyncio.create_task(tools[0].ainvoke({"x": 1, "y": 2}))
    await asyncio.wait_for(call_started.wait(), timeout=0.1)

    close_task = asyncio.create_task(configured_service.close())
    await asyncio.sleep(0)

    assert not close_task.done()
    assert state["sessions_exited"] == 1

    release_call.set()
    await in_flight_call
    await close_task

    assert state["sessions_exited"] == 2


@pytest.mark.asyncio
async def test_transport_failure_does_not_close_replaced_session(
    configured_service, monkeypatch
):
    """A stale failing call must not close a newer replacement session for the same server."""
    session_one = object()
    session_two = object()
    manager_one = _TrackedSessionManager(session_one)
    manager_two = _TrackedSessionManager(session_two)
    managers = [manager_one, manager_two]
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    third_started = asyncio.Event()
    release_first_failure = asyncio.Event()
    release_second_failure = asyncio.Event()
    release_third_call = asyncio.Event()

    async def fake_call_tool(session, tool_name, arguments=None):
        assert arguments is not None
        request_id = arguments["request_id"]
        if request_id == "first":
            first_started.set()
            await release_first_failure.wait()
            raise RuntimeError("first call failed")
        if request_id == "second":
            second_started.set()
            await release_second_failure.wait()
            raise RuntimeError("second call failed")
        third_started.set()
        await release_third_call.wait()
        return _make_call_tool_result(structured_content={"ok": True})

    shared_client_module = SimpleNamespace(
        open_mcp_session=MagicMock(side_effect=lambda config: managers.pop(0)),
        call_tool=AsyncMock(side_effect=fake_call_tool),
    )
    monkeypatch.setattr(
        svc_module,
        "shared_mcp_client_module",
        shared_client_module,
        raising=False,
    )

    config = SimpleNamespace(endpoint="http://localhost:8080/mcp")
    first_call = asyncio.create_task(
        configured_service._invoke_tool("math", config, "add", {"request_id": "first"})
    )
    await asyncio.wait_for(first_started.wait(), timeout=0.1)

    second_call = asyncio.create_task(
        configured_service._invoke_tool("math", config, "add", {"request_id": "second"})
    )
    await asyncio.wait_for(second_started.wait(), timeout=0.1)

    release_first_failure.set()
    with pytest.raises(RuntimeError, match="first call failed"):
        await first_call

    third_call = asyncio.create_task(
        configured_service._invoke_tool("math", config, "add", {"request_id": "third"})
    )
    await asyncio.wait_for(third_started.wait(), timeout=0.1)

    release_second_failure.set()
    with pytest.raises(RuntimeError, match="second call failed"):
        await second_call

    assert manager_one.exit_calls == 1
    assert manager_two.exit_calls == 0
    assert configured_service._call_sessions["math"] is session_two

    release_third_call.set()
    await third_call
    assert manager_two.exit_calls == 0

    await configured_service.close()
    assert manager_two.exit_calls == 1


@pytest.mark.asyncio
async def test_wrapped_tool_closes_session_on_call_failure(
    configured_service, monkeypatch
):
    """Wrapped MCP tools should always close the shared-client session when a call fails."""
    _, state, _ = _install_shared_client_module(
        monkeypatch,
        raw_tools=[_make_raw_mcp_tool()],
        call_side_effect=RuntimeError("tool failed"),
    )

    tools = await configured_service.get_tools()

    with pytest.raises(RuntimeError, match="tool failed"):
        await tools[0].ainvoke({"x": 1, "y": 2})

    assert state["sessions_entered"] == 2
    assert state["sessions_exited"] == 2
