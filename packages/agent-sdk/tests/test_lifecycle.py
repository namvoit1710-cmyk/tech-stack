import pytest
from fastapi.testclient import TestClient

from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app


class _StubRegistry:
    def __init__(self):
        self.registered: list[AgentRegistration] = []
        self.heartbeats: list[tuple[str, str]] = []
        self.deregistered: list[str] = []
        self.closed: bool = False

    async def register(self, registration: AgentRegistration) -> str:
        self.registered.append(registration)
        return "agent-stub-id-1"

    async def heartbeat(self, agent_id: str, status: str) -> None:
        self.heartbeats.append((agent_id, status))

    async def deregister(self, agent_id: str) -> None:
        self.deregistered.append(agent_id)

    async def close(self) -> None:
        self.closed = True


class _StubNotifier:
    def __init__(self):
        self.close_call_count: int = 0

    async def close(self) -> None:
        self.close_call_count += 1


class _StubToolRegistry:
    def __init__(self):
        self.close_call_count: int = 0

    async def close(self) -> None:
        self.close_call_count += 1


class _StubMCPClient:
    def __init__(self):
        self.close_call_count: int = 0

    async def close(self) -> None:
        self.close_call_count += 1


def test_push_gateway_notifier_close_called_on_shutdown():
    stub_notifier = _StubNotifier()
    container = {"_dependencies": {"push_gateway_notifier": stub_notifier}}
    app = create_agent_app(container)
    with TestClient(app):
        pass
    assert (
        stub_notifier.close_call_count == 1
    ), "push_gateway_notifier.close() should be awaited exactly once on shutdown"


def test_tool_registry_close_called_on_shutdown():
    stub_tool_registry = _StubToolRegistry()
    container = {"_dependencies": {"tool_registry": stub_tool_registry}}
    app = create_agent_app(container)
    with TestClient(app):
        pass
    assert (
        stub_tool_registry.close_call_count == 1
    ), "tool_registry.close() should be awaited exactly once on shutdown"


def test_mcp_client_close_called_on_shutdown():
    stub_mcp_client = _StubMCPClient()
    container = {"_dependencies": {"mcp_client": stub_mcp_client}}
    app = create_agent_app(container)
    with TestClient(app):
        pass
    assert (
        stub_mcp_client.close_call_count == 1
    ), "mcp_client.close() should be awaited exactly once on shutdown"


def test_health_endpoint_with_lifecycle():
    from agent_sdk.layer4_frameworks.config.app_config import settings

    stub_registry = _StubRegistry()
    container = {
        "_dependencies": {"agent_registry": stub_registry, "settings": settings}
    }
    app = create_agent_app(container)
    with TestClient(app) as client:
        assert (
            len(stub_registry.registered) == 1
        ), "Agent should be registered on startup"
        registered = stub_registry.registered[0]
        assert registered.agent_type
        if registered.capabilities:
            assert isinstance(registered.capabilities, list)
            assert isinstance(registered.capabilities[0], dict)
            assert "domain" in registered.capabilities[0]
            assert "action" in registered.capabilities[0]
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
    assert (
        len(stub_registry.deregistered) == 1
    ), "Agent should be deregistered on shutdown"
    assert stub_registry.deregistered[0] == "agent-stub-id-1"


@pytest.mark.asyncio
async def test_managed_agent_lifecycle_registers_with_registered_tool_ids():
    from unittest.mock import MagicMock

    from agent_sdk.layer3_adapters.presenters.agent_ops import managed_agent_lifecycle

    stub_registry = _StubRegistry()
    mock_settings = MagicMock()
    mock_settings.AGENT_ADVERTISED_URL = ""
    mock_settings.AGENT_ADVERTISE_URL = ""
    mock_settings.AGENT_ADVERTISED_SCHEME = ""
    mock_settings.AGENT_ADVERTISE_SCHEME = ""
    mock_settings.AGENT_ADVERTISED_PORT = ""
    mock_settings.AGENT_ADVERTISE_PORT = ""
    mock_settings.AGENT_TYPE = "planner"
    mock_settings.AGENT_VERSION = "1.0.0"
    mock_settings.SDK_VERSION = "1.0.0"
    mock_settings.AGENT_DOMAIN = "workflow"
    mock_settings.AGENT_ADVERTISED_HOST = "planner.local"
    mock_settings.SERVER_PORT = 8000
    mock_settings.CAPABILITIES = ["plan"]
    mock_settings.DESCRIPTION = "Planner"
    mock_settings.INPUT_SCHEMA = {}
    mock_settings.OUTPUT_SCHEMA = {}
    mock_settings.REQUIRED_PARAMETERS = []
    mock_settings.NEGATIVE_EXAMPLES = []
    mock_settings.ROUTING_TIMEOUT_SECONDS = 300.0
    mock_settings.REQUIRED_CONFIRMATION = True
    mock_settings.AGENT_KIND = "SUPERVISOR"
    mock_settings.IS_PUBLISHED = False
    mock_settings.ATTACHED_AGENT_IDS = []
    mock_settings.SYSTEM_PROMPT = "Plan work"
    mock_settings.MAX_CONCURRENCY = 1
    mock_settings.LLM_MODEL = None
    mock_settings.LLM_PROVIDER = None
    mock_settings.LLM_TEMPERATURE = None
    mock_settings.LLM_TIMEOUT = None
    mock_settings.LLM_MAX_TOKENS = None
    mock_settings.LLM_MAX_RETRIES = None
    mock_settings.QUEUE_NAME = ""
    mock_settings.QUEUE_REQUEST_TOPIC = ""
    mock_settings.QUEUE_REPLY_TOPIC = ""
    mock_settings.QUEUE_DELIVERY_HINTS = {}
    mock_settings.SUPPORTS_STREAMING = False
    mock_settings.SUPPORTS_HUMAN_IN_THE_LOOP = False
    mock_settings.HEARTBEAT_INTERVAL_SECONDS = 60

    async with managed_agent_lifecycle(
        {
            "agent_registry": stub_registry,
            "settings": mock_settings,
            "registered_tool_ids": {"search": "tool-1", "lookup": "tool-2"},
        }
    ):
        assert len(stub_registry.registered) == 1
        assert stub_registry.registered[0].tool_ids == ["tool-1", "tool-2"]


# ─────────────────────────────────────────────────────────────────────────────
# ToolAgentBuilder call_llm TOOL_SELECTED emission
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_agent_builder_call_llm_emits_tool_selected_when_tool_calls_present():
    """call_llm node must emit TOOL_SELECTED when the LLM response has tool_calls."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import HumanMessage

    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    ai_response = MagicMock()
    ai_response.tool_calls = [{"name": "my_tool", "id": "tc1", "args": {}}]
    ai_response.content = ""

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=ai_response)

    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def my_tool() -> str:
        """A test tool."""
        return "result"

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
        ToolAgentBuilder,
    )

    tab = ToolAgentBuilder(tools=[my_tool], llm=mock_llm)

    original_add_node = None

    import agent_sdk.layer4_frameworks.graph.agent_graph_builder as gb

    original_add_node = gb.AgentGraphBuilder.add_node

    captured_nodes = {}

    def capturing_add_node(self, name, fn):
        captured_nodes[name] = fn
        return original_add_node(self, name, fn)

    gb.AgentGraphBuilder.add_node = capturing_add_node
    try:
        tab.compile()
    finally:
        gb.AgentGraphBuilder.add_node = original_add_node

    assert "call_llm" in captured_nodes, "call_llm node must be registered"
    call_llm_node = captured_nodes["call_llm"]

    state = {"messages": [HumanMessage(content="hi")]}
    with workflow_event_scope(emitter):
        await call_llm_node(state)

    published_types = [
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    ]
    assert (
        "TOOL_SELECTED" in published_types
    ), f"Expected TOOL_SELECTED in {published_types}"


@pytest.mark.asyncio
async def test_tool_agent_builder_call_llm_no_tool_selected_when_no_tool_calls():
    """call_llm node must NOT emit TOOL_SELECTED when LLM response has no tool_calls."""
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import HumanMessage

    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer2_application.services.workflow_event_runtime import (
        workflow_event_scope,
    )

    mock_publisher = AsyncMock()
    emitter = WorkflowEventEmitter(publisher=mock_publisher)

    ai_response = MagicMock()
    ai_response.tool_calls = []
    ai_response.content = "Final answer"

    mock_llm = MagicMock()
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    mock_llm.ainvoke = AsyncMock(return_value=ai_response)

    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def other_tool() -> str:
        """Another tool."""
        return "y"

    import agent_sdk.layer4_frameworks.graph.agent_graph_builder as gb

    original_add_node = gb.AgentGraphBuilder.add_node
    captured_nodes = {}

    def capturing_add_node(self, name, fn):
        captured_nodes[name] = fn
        return original_add_node(self, name, fn)

    gb.AgentGraphBuilder.add_node = capturing_add_node
    try:
        from agent_sdk.layer4_frameworks.graph.tool_agent_builder import (
            ToolAgentBuilder,
        )

        tab = ToolAgentBuilder(tools=[other_tool], llm=mock_llm)
        tab.compile()
    finally:
        gb.AgentGraphBuilder.add_node = original_add_node

    assert "call_llm" in captured_nodes
    call_llm_node = captured_nodes["call_llm"]

    state = {"messages": [HumanMessage(content="hi")]}
    with workflow_event_scope(emitter):
        await call_llm_node(state)

    published_types = [
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    ]
    assert "TOOL_SELECTED" not in published_types
