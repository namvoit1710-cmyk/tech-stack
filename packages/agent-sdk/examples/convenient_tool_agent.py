"""convenient_tool_agent.py — AgentGraphBuilder-based tool agent example.

This example demonstrates how to build a custom tool-calling agent graph
using only the agent SDK — no direct imports from ``langgraph`` or
``langchain`` required.  Everything needed (``AgentGraphBuilder``,
``ToolAgentState``, ``ToolNode``, ``tools_condition``, ``END``, ``tool``,
``make_openai_service``, and ``run_agent``) is re-exported from
``agent_sdk``.

This approach gives full control over the graph topology while keeping
the agent code free of low-level framework imports.

Required environment variables:
    OPENAI_API_KEY  — your OpenAI API key

Usage::

    OPENAI_API_KEY=sk-... python convenient_tool_agent.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent_sdk import (
    END,
    AgentGraphBuilder,
    ToolAgentState,
    make_openai_service,
    run_agent,
    tool,
)

SYSTEM_PROMPT = (
    "You are a workflow assistant. "
    "Use the available tools to help the user complete tasks."
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


@tool
def echo_task(message: str) -> dict:
    """Echo a message back.

    Args:
        message: The message to echo.
    """
    return {"status": "COMPLETED", "echo": message}


def build_graph(llm_service=None):
    """Build the tool-calling graph using AgentGraphBuilder.

    Graph topology::

        [prepare] → [agent] → tools_condition → [tools] → [agent] (loop)
                                              → [format_response] → END

    Args:
        llm_service: an ILLMService instance (defaults to make_openai_service()).

    Returns:
        A compiled LangGraph StateGraph.
    """
    if llm_service is None:
        llm_service = make_openai_service()

    tools = [wait_task, echo_task]
    llm = llm_service.get_chat_client().bind_tools(tools)

    def prepare(state: ToolAgentState) -> dict:
        messages = state.get("messages", [])
        if messages:
            return {"transport_state": "PROCESSING"}
        msgs = []
        if SYSTEM_PROMPT:
            msgs.append({"role": "system", "content": SYSTEM_PROMPT})
        msgs.append({"role": "user", "content": state.get("message", "")})
        return {"messages": msgs, "transport_state": "PROCESSING"}

    async def agent(state: ToolAgentState) -> dict:
        response = await llm.ainvoke(state.get("messages", []))
        return {"messages": [response]}

    def format_response(state: ToolAgentState) -> dict:
        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        content = getattr(last, "content", str(last)) if last else ""
        tool_results = [
            {"tool": getattr(m, "name", ""), "result": m.content}
            for m in messages
            if getattr(m, "type", None) == "tool"
        ]
        return {
            "formatted_response": {"content": content, "tool_results": tool_results},
            "tool_results": tool_results,
            "transport_state": "COMPLETED",
        }

    builder = (
        AgentGraphBuilder(state_schema=ToolAgentState)
        .add_tools(tools)
        .add_node("prepare", prepare)
        .add_node("agent", agent)
        .add_tool_node("tools")
        .add_node("format_response", format_response)
        .set_entry_point("prepare")
        .add_edge("prepare", "agent")
        .add_tools_condition("agent", tools_node="tools", next_node="format_response")
        .add_edge("tools", "agent")
        .add_edge("format_response", END)
    )
    return builder.compile()


def main() -> None:
    run_agent(agent_graph=build_graph())


if __name__ == "__main__":
    main()
