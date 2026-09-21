"""supervisor_agent_example.py — full supervisor agent with AgentCallCoordinator.

Demonstrates how to build a supervisor agent that automatically handles
AGENT_CALL interrupts when a sub-agent is called via RemoteAgentTool.

Without AgentCallCoordinator the supervisor must manually detect the AGENT_CALL
interrupt, call the sub-agent over HTTP, and POST /resume with the result.
With AgentCallCoordinator this loop is automated: execute_with_auto_resume()
handles it transparently, preserving structured business payloads verbatim.

Three graph-building approaches are shown (simplest → most control):

    ``build_supervisor_graph()``
        Static — hardcodes a single RemoteAgentTool.  Uses ToolAgentBuilder.

    ``build_dynamic_supervisor_graph()``
        Dynamic discovery — discovers sub-agents at startup via
        AgentDiscoveryService.  Uses ToolAgentBuilder.

    ``build_custom_agentgraphbuilder_supervisor()``
        Full control — uses AgentGraphBuilder directly with dynamic
        discovery, a custom guardrail node, and intent-based conditional
        routing.  Demonstrates why you would drop down from ToolAgentBuilder.

Key SDK symbols used (no direct langgraph or internal imports needed):
    ``AgentCallCoordinator``    — auto-handles AGENT_CALL interrupt cycles
    ``AgentGraphBuilder``       — low-level graph builder for custom topologies
    ``IAgentEndpointResolver``  — port for resolving agent_id → HTTP base URL
    ``AgentCapability``         — routing metadata for a sub-agent
    ``ToolAgentBuilder``        — convenience builder for the tool-calling graph
    ``ToolAgentState``          — standard state schema for tool-calling graphs
    ``RemoteAgentTool``         — LangChain tool that triggers AGENT_CALL interrupt
    ``make_openai_service``     — creates ILLMService from environment
    ``create_checkpointer``     — creates checkpointer from settings
    ``settings``                — SDK settings object

Flow diagram::

    User request
          ↓
    AgentCallCoordinator.execute_with_auto_resume(execute_uc, request)
          ↓
    ExecuteAgentUseCase.execute() → AGENT_CALL interrupt detected
          ↓  (auto-loop)
    _call_sub_agent(agent_call) → POST sub-agent /api/v1/execute
          ↓
    ResumeAgentUseCase.execute(resume_input)  ← verbatim sub-agent response
          ↓
    Final ExecuteAgentOutput (no interrupt)

Requirements:
    OPENAI_API_KEY, REGISTRY_URL

Important production note:
    ``run_agent(agent_graph=...)`` by itself is not enough for a supervisor that
    delegates to sub-agents via ``RemoteAgentTool``. To make the supervisor
    pattern work end-to-end, the server must replace the default
    ``execute_agent`` use case in the SDK app container with a coordinator-backed
    wrapper that calls ``AgentCallCoordinator.execute_with_auto_resume()``.
    This example includes that wiring in ``build_supervisor_container()``.

Usage::

    OPENAI_API_KEY=sk-... REGISTRY_URL=http://localhost:8080 \\
        python examples/supervisor_agent_example.py

    # Demo mode (no env vars needed):
    python examples/supervisor_agent_example.py --demo
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import uvicorn

from agent_sdk import (
    END,
    AgentCallCoordinator,
    AgentCapability,
    AgentGraphBuilder,
    HttpAgentRegistry,
    IAgentEndpointResolver,
    RemoteAgentTool,
    ToolAgentBuilder,
    ToolAgentState,
    build_app_container,
    create_agent_app,
    create_checkpointer,
    make_openai_service,
    settings,
)

# ---------------------------------------------------------------------------
# Stub endpoint resolver for demo/tests
# ---------------------------------------------------------------------------


class FixedEndpointResolver(IAgentEndpointResolver):
    """Resolves every agent_id to a fixed base URL.

    In production replace this with a real resolver backed by your service
    registry (e.g. Kubernetes DNS lookup, Consul, or your orchestrator's
    /agents endpoint).
    """

    def __init__(self, base_url: str = "http://sub-agent:36000") -> None:
        self._url = base_url

    async def resolve_endpoint(self, agent_id: str) -> str:
        return self._url


# ---------------------------------------------------------------------------
# Stub agent registry for demo
# ---------------------------------------------------------------------------


class _MockAgentRegistry:
    async def register(self, registration) -> str:
        return "agent-uuid-sub-001"

    async def heartbeat(self, agent_id: str, status: str) -> None:
        pass

    async def deregister(self, agent_id: str) -> None:
        pass

    async def close(self) -> None:
        pass

    async def resolve_agent_id(self, agent_type: str) -> str | None:
        return "agent-uuid-sub-001"

    async def list_active_agents(self, domain: str | None = None) -> list[dict]:
        """Return mock agent list for demo purposes."""
        return [
            {
                "agent_id": "agent-uuid-sub-001",
                "name": "mock-sub-agent-1.0",
                "description": "A mock sub-agent for demo purposes",
                "domain": domain or "general",
                "configuration": {
                    "agent_type": "mock-sub-agent",
                    "endpoint_url": "http://localhost:9001",
                },
            },
        ]


# ---------------------------------------------------------------------------
# Agent graph using RemoteAgentTool
# ---------------------------------------------------------------------------


def build_supervisor_graph(registry=None):
    """Build a supervisor graph with a remote sub-agent tool.

    The supervisor delegates work to a remote file-processor agent.
    AgentCallCoordinator will auto-handle the AGENT_CALL interrupt cycle
    when the coordinator.execute_with_auto_resume() entry point is used.

    Args:
        registry: IAgentRegistry for resolving agent_type → agent_id.
                  Defaults to a MockAgentRegistry for demo.

    Returns:
        Compiled LangGraph graph.
    """
    if registry is None:
        registry = _MockAgentRegistry()

    remote_tool = RemoteAgentTool(
        remote_agent_type="file-processor-agent",
        registry=registry,
        name="call_file_processor",
        description=(
            "Delegate file processing tasks to the file-processor agent. "
            "Input should be a JSON string with file_id and instructions."
        ),
        # For long-lived supervisors, avoid caching agent_id forever.
        # A finite TTL or explicit invalidation prevents stale IDs after
        # downstream agent restarts.
        resolve_ttl_seconds=300,
    )

    llm_service = make_openai_service()
    builder = ToolAgentBuilder(
        llm_service=llm_service,
        tools=[remote_tool],
        system_prompt=(
            "You are a supervisor agent. "
            "When the user asks you to process a file, call the file processor tool."
        ),
    )
    checkpointer = create_checkpointer(settings)
    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Dynamic discovery — zero hardcoded agents
# ---------------------------------------------------------------------------


async def build_dynamic_supervisor_graph(registry=None):
    """Build a supervisor graph by discovering agents from the registry.

    Instead of hardcoding RemoteAgentTool instances, this function:
    1. Queries the registry for all active agents
    2. Generates AgentCapability and RemoteAgentTool instances automatically
    3. Compiles a ToolAgentBuilder graph with the discovered tools

    Capabilities are also used by AgentCallCoordinator for:
    - Per-agent HTTP timeouts (instead of a flat default)
    - Required parameter validation before calling sub-agents

    Zero code changes needed when agents are added or removed — just
    restart the supervisor and it discovers the new set of agents.

    Args:
        registry: IAgentRegistry for listing and resolving agents.
                  Defaults to a MockAgentRegistry for demo.

    Returns:
        Tuple of (compiled_graph, capabilities_dict).
    """
    from agent_sdk import AgentDiscoveryService, default_tool_factory

    if registry is None:
        registry = _MockAgentRegistry()

    discovery = AgentDiscoveryService(
        registry=registry,
        exclude_agent_types=["supervisor"],
        tool_factory=default_tool_factory,
    )
    capabilities, tools = await discovery.discover()

    if not tools:
        raise RuntimeError("No agents discovered — is any agent running?")

    caps_dict = {cap.agent_type: cap for cap in capabilities}
    print(f"Discovered {len(capabilities)} agents:")
    for cap in capabilities:
        print(f"  - {cap.agent_type}: {cap.description[:60]}...")

    llm_service = make_openai_service()
    builder = ToolAgentBuilder(
        llm_service=llm_service,
        tools=tools,
        system_prompt=(
            "You are a supervisor agent. "
            "Use the available tools to delegate tasks to specialized agents."
        ),
    )
    checkpointer = create_checkpointer(settings)
    compiled_graph = builder.compile(checkpointer=checkpointer)
    return compiled_graph, caps_dict


# ---------------------------------------------------------------------------
# Custom AgentGraphBuilder — full graph control with dynamic discovery
# ---------------------------------------------------------------------------


async def build_custom_agentgraphbuilder_supervisor(registry=None):
    """Build a supervisor with AgentGraphBuilder, dynamic discovery, and
    intent-based routing.

    Unlike ``build_dynamic_supervisor_graph()`` (which uses ToolAgentBuilder),
    this function uses **AgentGraphBuilder directly** for full control over the
    graph topology.  It adds two custom features that ToolAgentBuilder cannot
    express:

    1. **Guardrail node** (``classify_intent``) — validates user input before
       the LLM sees it.  Empty or trivially short messages are rejected
       immediately without burning an LLM call.
    2. **Intent-based routing** (``intent_router``) — a conditional edge that
       inspects the classified intent and routes the request:
       - ``"delegate"`` → send to the LLM, which picks the right discovered
         sub-agent tool.
       - ``"reject"``  → return an error response, skipping the LLM entirely.

    Graph topology::

        [prepare] → [classify_intent] → conditional:
              ├── "delegate" → [call_llm] → tools_condition → [tools] ↩ [call_llm]
              │                                              → [format_response] → END
              └── "reject"  → [reject_response] → END

    Why use AgentGraphBuilder instead of ToolAgentBuilder?
        - Custom guard-rail / validation nodes *before* the LLM
        - Intent-based routing (skip the LLM for certain inputs)
        - Full control over node naming, edge wiring, and graph shape
        - Ability to add subgraphs (``add_subgraph``), mapped subgraphs, or
          complex branching that ToolAgentBuilder's linear topology can't model

    Args:
        registry: IAgentRegistry for listing and resolving agents.
                  Defaults to a MockAgentRegistry for demo.

    Returns:
        Tuple of (compiled_graph, capabilities_dict).
    """
    from agent_sdk import AgentDiscoveryService, default_tool_factory

    if registry is None:
        registry = _MockAgentRegistry()

    # ── 1. Discover available agents dynamically ───────────────────────
    discovery = AgentDiscoveryService(
        registry=registry,
        exclude_agent_types=["supervisor"],
        tool_factory=default_tool_factory,
    )
    capabilities, tools = await discovery.discover()

    if not tools:
        raise RuntimeError("No agents discovered — is any agent running?")

    caps_dict = {cap.agent_type: cap for cap in capabilities}
    print(f"[custom] Discovered {len(capabilities)} agent(s):")
    for cap in capabilities:
        print(f"  - {cap.agent_type}: {cap.description[:60]}...")

    # ── 2. Bind discovered tools to the LLM ────────────────────────────
    llm_service = make_openai_service()
    llm = llm_service.get_chat_client().bind_tools(tools)

    # ── 3. Define custom node functions ─────────────────────────────────

    def prepare(state: ToolAgentState) -> dict:
        """Format raw user input into chat messages.

        The system prompt is built dynamically from the discovered
        capabilities — no hardcoded agent names.
        """
        messages = state.get("messages", [])
        if messages:
            return {"transport_state": "PROCESSING"}

        agent_list = ", ".join(
            f"{cap.agent_type} ({cap.description[:60]})" for cap in capabilities
        )
        return {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a supervisor agent. "
                        "Route tasks to specialized agents. "
                        f"Available agents: {agent_list}"
                    ),
                },
                {"role": "user", "content": state.get("message", "")},
            ],
            "transport_state": "PROCESSING",
        }

    def classify_intent(state: ToolAgentState) -> dict:
        """Guardrail node — validate input and classify intent.

        In production, replace the keyword heuristic with an LLM
        classifier, embedding similarity search, or a lightweight ML
        model.  The key idea is that this runs *before* the expensive
        LLM call, so it can reject bad input cheaply.
        """
        # Nothing to store in state — intent_router reads messages
        # directly.  This node exists as a hook point for more
        # sophisticated validation logic (e.g. PII scrubbing, rate
        # limiting, or injection detection).
        return {}

    def intent_router(state: ToolAgentState) -> str:
        """Conditional-edge router: decides where to send the request.

        Returns one of:
            ``"delegate"`` — valid input, send to the LLM for
                             tool-based delegation to a sub-agent.
            ``"reject"``   — invalid input, skip the LLM entirely.
        """
        messages = state.get("messages", [])
        user_msg = ""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                user_msg = msg.get("content", "")
                break
            if hasattr(msg, "type") and msg.type == "human":
                user_msg = getattr(msg, "content", "")
                break

        if not user_msg.strip() or len(user_msg.strip()) < 3:
            return "reject"
        return "delegate"

    async def call_llm(state: ToolAgentState) -> dict:
        """Call the LLM with the dynamically-discovered agent tools."""
        response = await llm.ainvoke(state.get("messages", []))
        return {"messages": [response]}

    def reject_response(state: ToolAgentState) -> dict:
        """Terminal node for rejected requests — no LLM call needed."""
        return {
            "formatted_response": {
                "content": (
                    "Request rejected: please provide a valid task "
                    "description (at least 3 characters)."
                ),
                "tool_results": [],
            },
            "transport_state": "COMPLETED",
        }

    def format_response(state: ToolAgentState) -> dict:
        """Extract the final LLM response and any tool results."""
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        content = getattr(last, "content", str(last)) if last else ""
        tool_results = [
            {"tool": getattr(m, "name", ""), "result": m.content}
            for m in messages
            if getattr(m, "type", None) == "tool"
        ]
        return {
            "formatted_response": {
                "content": content,
                "tool_results": tool_results,
            },
            "tool_results": tool_results,
            "transport_state": "COMPLETED",
        }

    # ── 4. Assemble the graph with AgentGraphBuilder ───────────────────
    builder = (
        AgentGraphBuilder(state_schema=ToolAgentState)
        .add_tools(tools)
        # Nodes
        .add_node("prepare", prepare)
        .add_node("classify_intent", classify_intent)
        .add_node("call_llm", call_llm)
        .add_tool_node("tools")
        .add_node("reject_response", reject_response)
        .add_node("format_response", format_response)
        # Entry point
        .set_entry_point("prepare")
        # Edges: prepare → classify_intent
        .add_edge("prepare", "classify_intent")
        # Intent-based conditional routing
        .add_conditional_edges(
            "classify_intent",
            intent_router,
            {"delegate": "call_llm", "reject": "reject_response"},
        )
        # Standard tool-calling loop: LLM ↔ tools
        .add_tools_condition(
            "call_llm", tools_node="tools", next_node="format_response"
        )
        .add_edge("tools", "call_llm")
        # Terminal edges
        .add_edge("reject_response", END)
        .add_edge("format_response", END)
    )

    checkpointer = create_checkpointer(settings)
    compiled_graph = builder.compile(checkpointer=checkpointer)
    return compiled_graph, caps_dict


# ---------------------------------------------------------------------------
# Demo — shows AgentCallCoordinator auto-resume loop (without LLM)
# ---------------------------------------------------------------------------


async def demo_coordinator() -> None:
    """Demonstrate AgentCallCoordinator auto-resume logic with mocks.

    Mocks ExecuteAgentUseCase and ResumeAgentUseCase to simulate an
    AGENT_CALL interrupt followed by successful resume, without needing
    OPENAI_API_KEY or a running agent.
    """
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from agent_sdk import (
        ExecuteAgentInput,
        ExecuteAgentOutput,
        HitlInterruptPayload,
        InterruptType,
        ResumeAgentUseCase,
    )

    interrupt_payload = HitlInterruptPayload(
        type=InterruptType.AGENT_CALL,
        value={
            "type": "AGENT_CALL",
            "agent_id": "agent-uuid-sub-001",
            "agent_type": "file-processor-agent",
            "input": {"file_id": "f-42", "instructions": "Summarise"},
        },
        thread_id="thread-demo-001",
        interrupt_id="intr-001",
    )

    mock_execute_uc = MagicMock()
    mock_execute_uc.execute = AsyncMock(
        side_effect=[
            ExecuteAgentOutput(
                message="",
                status="interrupted",
                interrupted=True,
                interrupt_payload=interrupt_payload,
            ),
        ]
    )

    mock_resume_uc = MagicMock(spec=ResumeAgentUseCase)
    mock_resume_uc.execute = AsyncMock(
        return_value=MagicMock(
            message="File processed: f-42",
            status="success",
            agent_data={"file_id": "f-42", "content": "Summary text", "message": "ok"},
            error=None,
            error_code=None,
            correlation_id=None,
            duration_ms=42,
            interrupted=False,
            interrupt_payload=None,
        )
    )

    mock_http_client = AsyncMock(spec=httpx.AsyncClient)
    mock_http_client.post = AsyncMock(
        return_value=MagicMock(
            json=lambda: {
                "file_id": "f-42",
                "content": "Summary text",
                "message": "ok",
            },
            raise_for_status=lambda: None,
        )
    )

    resolver = FixedEndpointResolver("http://file-processor:36000")

    # Build per-agent capabilities for the coordinator.
    # In production these come from AgentDiscoveryService.discover().
    caps_dict = {
        "file-processor-agent": AgentCapability(
            agent_type="file-processor-agent",
            name="File Processor",
            description="Processes file operations.",
            input_schema={
                "type": "object",
                "properties": {"file_id": {"type": "string"}},
            },
            output_schema={},
            required_parameters=["file_id"],
            negative_examples=["user is asking a general question not about files"],
            timeout_seconds=60.0,
        ),
    }

    coordinator = AgentCallCoordinator(
        endpoint_resolver=resolver,
        resume_use_case=mock_resume_uc,
        http_client=mock_http_client,
        timeout=30.0,
        capabilities=caps_dict,
    )

    request = ExecuteAgentInput(message="Process file f-42 and summarise it.")
    result = await coordinator.execute_with_auto_resume(mock_execute_uc, request)

    assert result.status == "success", f"Expected 'success', got: {result.status}"
    assert "f-42" in result.message, f"Expected file id in message: {result.message}"
    print(f"Demo passed — result.status={result.status!r}, message={result.message!r}")


class CoordinatorExecuteAgentUseCase:
    """Wrap the default execute use case with AgentCallCoordinator.

    This is the missing production wiring that turns a tool-calling graph into a
    real supervisor server. Without this wrapper, ``RemoteAgentTool`` still emits
    ``AGENT_CALL`` interrupts, but the server will just return the interrupt to
    the caller instead of auto-calling the sub-agent and resuming the graph.
    """

    def __init__(self, coordinator, execute_agent):
        self._coordinator = coordinator
        self._execute_agent = execute_agent

    async def execute(self, request):
        return await self._coordinator.execute_with_auto_resume(
            self._execute_agent, request
        )


class RegistryBackedEndpointResolver(IAgentEndpointResolver):
    """Resolve current endpoint URLs from the registry's active agent list.

    This mirrors the production pattern used by real supervisors: the
    coordinator receives an ``agent_id`` from the interrupt payload and must map
    it back to the agent's current base URL.
    """

    def __init__(self, registry):
        self._registry = registry

    async def resolve_endpoint(self, agent_id: str) -> str:
        agents = await self._registry.list_active_agents()
        for agent in agents:
            if agent.get("agent_id") != agent_id:
                continue

            endpoint = agent.get("configuration", {}).get("endpoint_url")
            if endpoint:
                return endpoint

            endpoint = agent.get("endpoint_url")
            if endpoint:
                return endpoint

            health_endpoint = agent.get("health_endpoint") or ""
            if health_endpoint.endswith("/health"):
                return health_endpoint.removesuffix("/health")

            raise ValueError(f"No endpoint URL found for active agent_id '{agent_id}'")

        raise ValueError(f"No active agent found with agent_id '{agent_id}'")


def build_supervisor_container() -> dict:
    """Build the real server container for a supervisor agent.

    This is the recommended production setup:
    1. build the supervisor graph with ``RemoteAgentTool`` instances
    2. build the normal SDK app container
    3. create ``AgentCallCoordinator`` with a registry-backed endpoint resolver
    4. replace ``container['execute_agent']`` with a coordinator-backed wrapper
    """

    registry = HttpAgentRegistry()
    graph = build_supervisor_graph(registry=registry)
    container = build_app_container(
        agent_graph=graph,
        extra_dependencies={"agent_registry": registry, "settings": settings},
    )

    deps = container["_dependencies"]
    execute_agent = container["execute_agent"]
    resume_agent = container["resume_agent"]

    coordinator = AgentCallCoordinator(
        endpoint_resolver=RegistryBackedEndpointResolver(registry),
        resume_use_case=resume_agent,
        http_client=httpx.AsyncClient(),
        logger=deps["logger"],
        capabilities={},
    )

    container["execute_agent"] = CoordinatorExecuteAgentUseCase(
        coordinator=coordinator,
        execute_agent=execute_agent,
    )
    container["coordinator"] = coordinator
    return container


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the supervisor agent server with coordinator wiring."""
    container = build_supervisor_container()
    app = create_agent_app(container)
    uvicorn.run(app, host=settings.SERVER_HOST, port=settings.SERVER_PORT)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        asyncio.run(demo_coordinator())
    else:
        main()
