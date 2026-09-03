"""minimal_tool_agent.py — canonical low-boilerplate tool-agent example.

This example shows what an agent author needs to write when using the SDK:
only task-specific code.  There is no need to import or configure OpenAI /
LangChain classes directly — the SDK handles provider setup from environment
variables.

Required environment variables:
    OPENAI_API_KEY          — your OpenAI API key

Usage::

    OPENAI_API_KEY=sk-... python minimal_tool_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import (
    ToolAgentBuilder,
    make_openai_service,
    run_agent,
    tool,
)


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


def main() -> None:
    builder = ToolAgentBuilder(
        llm_service=make_openai_service(),
        tools=[wait_task],
        system_prompt=(
            "You are a workflow assistant. "
            "Use the available tools to help the user complete tasks."
        ),
    )

    run_agent(agent_graph=builder.compile())


if __name__ == "__main__":
    main()
