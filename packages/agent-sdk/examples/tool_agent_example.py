"""tool_agent_example.py — ToolAgentBuilder with local @tool functions.

Demonstrates the canonical pattern for building a tool-calling agent using
``ToolAgentBuilder`` and the ``@tool`` decorator from ``agent_sdk``.

Key SDK symbols used:
    ``ToolAgentBuilder`` — convenience builder for a ReAct-style tool-calling graph
    ``make_openai_service`` — creates an ``ILLMService`` from environment settings
    ``run_agent``            — starts the FastAPI server
    ``tool``                 — decorator to define local LangChain-compatible tools

Usage::

    OPENAI_API_KEY=sk-... python examples/tool_agent_example.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import ToolAgentBuilder, make_openai_service, run_agent, tool


@tool
def wait_task(wait_type: str, duration_seconds: int = 60) -> dict:
    """Execute a wait operation for a given duration.

    Args:
        wait_type: The type of wait (e.g. 'fixed_duration').
        duration_seconds: How long to wait in seconds.
    """
    return {
        "status": "COMPLETED",
        "wait_type": wait_type,
        "actual_duration_seconds": duration_seconds,
    }


@tool
def echo_task(message: str) -> dict:
    """Echo a message back.

    Args:
        message: The message to echo.
    """
    return {"status": "COMPLETED", "echo": message}


def main() -> None:
    tools = [wait_task, echo_task]
    print(f"Registered {len(tools)} local tools: {[t.name for t in tools]}")
    llm_service = make_openai_service()

    def build_graph(deps: dict):
        builder = ToolAgentBuilder(
            llm=deps["llm"],
            tools=deps["worker_tools"],
            system_prompt="You are a workflow assistant. Use available tools to help the user complete tasks.",
        )
        return builder.compile()

    run_agent(
        agent_graph_factory=build_graph,
        extra_dependencies={"openai_service": llm_service},
        local_tools=tools,
    )


if __name__ == "__main__":
    main()
