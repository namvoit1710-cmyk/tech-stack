"""remote_agent_example.py — RemoteAgentTool example.

Demonstrates how to build an agent that delegates work to a remote
sub-agent using ``RemoteAgentTool``.

When the LLM decides to call the remote tool, ``RemoteAgentTool._arun``
first resolves the target agent's concrete ``agent_id`` via
``IAgentRegistry``, then calls ``interrupt()`` with an ``AGENT_CALL``
payload.  The orchestrator intercepts the interrupt, forwards the call to
the resolved agent, collects the reply, and resumes the graph with the
result.

Key SDK symbols used (no direct langgraph imports needed):
    ``RemoteAgentTool``     — LangChain ``BaseTool`` that triggers AGENT_CALL
    ``IAgentRegistry``      — interface for resolving agent_type → agent_id
    ``ToolAgentBuilder``    — convenience builder for the tool-calling graph
    ``make_openai_service`` — creates an ``ILLMService`` from environment
    ``run_agent``           — starts the FastAPI server

This example shows two things:

1. **Production wiring** — ``main()`` uses ``HttpAgentRegistry`` backed by
   the real orchestrator registry service (``REGISTRY_URL`` env var).

2. **Mock orchestrator** — ``demo_with_mock_registry()`` is a standalone,
   self-contained demo that runs without any external services.  It wires a
   fake registry and shows the interrupt payload that would be sent to the
   orchestrator.

Usage::

    # Production (requires OPENAI_API_KEY + REGISTRY_URL):
    OPENAI_API_KEY=sk-... REGISTRY_URL=http://localhost:8080 \\
        python examples/remote_agent_example.py

    # Mock demo (no env vars needed):
    python examples/remote_agent_example.py --demo
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import (
    IAgentRegistry,
    RemoteAgentTool,
    ToolAgentBuilder,
    make_openai_service,
    run_agent,
)

# ---------------------------------------------------------------------------
# Mock registry for local demos / tests
#
# In production this is replaced by HttpAgentRegistry, which queries the
# orchestrator's /api/agents endpoint to resolve agent_type → agent_id.
# ---------------------------------------------------------------------------


class MockAgentRegistry:
    """In-memory registry that returns a fixed agent_id for any agent_type."""

    def __init__(self, fixed_id: str = "agent-uuid-0042") -> None:
        self._fixed_id = fixed_id

    async def register(self, registration) -> str:
        return self._fixed_id

    async def heartbeat(self, agent_id: str, status: str) -> None:
        pass

    async def deregister(self, agent_id: str) -> None:
        pass

    async def close(self) -> None:
        pass

    async def resolve_agent_id(self, agent_type: str) -> str | None:
        print(f"[MockAgentRegistry] Resolving '{agent_type}' → '{self._fixed_id}'")
        return self._fixed_id


# ---------------------------------------------------------------------------
# Building the agent graph
# ---------------------------------------------------------------------------


def build_agent_with_remote_tool(registry: IAgentRegistry):
    """Return a compiled graph that can call a remote sub-agent.

    The graph uses ``ToolAgentBuilder`` — the simplest path when you only
    need tools without custom node topology.

    Args:
        registry: an ``IAgentRegistry`` implementation; in production this
                  is ``HttpAgentRegistry``; in tests a ``MockAgentRegistry``.

    Returns:
        A compiled LangGraph StateGraph.
    """
    remote_tool = RemoteAgentTool(
        remote_agent_type="data-processor-agent",
        registry=registry,
        name="call_data_processor",
        description=(
            "Call the remote data-processor agent to transform or analyse data. "
            "Input should be a plain-text description of the data task."
        ),
    )

    llm_service = make_openai_service()
    builder = ToolAgentBuilder(
        llm_service=llm_service,
        tools=[remote_tool],
        system_prompt=(
            "You are an orchestrator agent. "
            "When the user asks you to process data, use call_data_processor."
        ),
    )
    return builder.compile()


# ---------------------------------------------------------------------------
# Mock demo — runs without external services
# ---------------------------------------------------------------------------


async def demo_with_mock_registry() -> None:
    """Demonstrate RemoteAgentTool resolution and interrupt payload.

    This demo bypasses the LLM and calls _arun() directly so it can run
    without OPENAI_API_KEY or a live registry service.
    """
    from unittest.mock import patch

    registry = MockAgentRegistry(fixed_id="agent-uuid-0042")
    remote_tool = RemoteAgentTool(
        remote_agent_type="data-processor-agent",
        registry=registry,
        name="call_data_processor",
        description="Delegate to the data-processor agent.",
    )

    print("Calling RemoteAgentTool._arun with mocked interrupt …")
    captured: list[dict] = []

    def fake_interrupt(payload: dict):
        captured.append(payload)
        return "mock-orchestrator-response"

    with patch(
        "agent_sdk.layer4_frameworks.ai.remote_agent_tool.interrupt",
        side_effect=fake_interrupt,
    ):
        result = await remote_tool._arun(input="Summarise the sales CSV.")

    payload = captured[0]
    print("\nInterrupt payload sent to orchestrator:")
    for k, v in payload.items():
        print(f"  {k}: {v!r}")

    assert payload["type"] == "AGENT_CALL"
    assert payload["agent_id"] == "agent-uuid-0042"
    assert payload["input"] == "Summarise the sales CSV."
    assert result == "mock-orchestrator-response"
    print("\nDemo passed — interrupt payload verified.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Start the agent server with RemoteAgentTool wired to HttpAgentRegistry."""
    from agent_sdk import HttpAgentRegistry

    # HttpAgentRegistry() is a zero-argument constructor; it reads REGISTRY_URL
    # from settings automatically.  No kwargs are supported.
    registry = HttpAgentRegistry()
    compiled_graph = build_agent_with_remote_tool(registry=registry)
    run_agent(agent_graph=compiled_graph)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        asyncio.run(demo_with_mock_registry())
    else:
        main()
