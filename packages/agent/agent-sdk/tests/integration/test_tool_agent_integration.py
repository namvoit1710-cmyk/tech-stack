import pytest
from langchain_core.messages import AIMessage

from agent_sdk import ToolAgentBuilder, tool


@tool
def wait_tool(wait_type: str, duration_seconds: int = 60) -> dict:
    """Execute a wait operation for a given duration."""
    return {
        "status": "COMPLETED",
        "wait_type": wait_type,
        "actual_duration_seconds": duration_seconds,
    }


class FakeLLMWithLocalToolCall:
    def __init__(self):
        self._call_count = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self._call_count += 1
        if self._call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "wait_tool",
                        "args": {"wait_type": "fixed_duration", "duration_seconds": 5},
                        "id": "call_wait_1",
                    }
                ],
            )
        return AIMessage(content="The wait completed successfully.")

    async def ainvoke(self, messages):
        self._call_count += 1
        if self._call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "wait_tool",
                        "args": {"wait_type": "fixed_duration", "duration_seconds": 5},
                        "id": "call_wait_1",
                    }
                ],
            )
        return AIMessage(content="The wait completed successfully.")


@pytest.mark.asyncio
async def test_local_tool_invocation():
    """A local @tool function should be invocable and return its result."""
    result = await wait_tool.ainvoke(
        {"wait_type": "fixed_duration", "duration_seconds": 5}
    )
    assert result["status"] == "COMPLETED"
    assert result["actual_duration_seconds"] == 5


@pytest.mark.asyncio
async def test_full_tool_agent_loop_with_local_tool():
    """End-to-end graph: LLM calls a local @tool, graph returns COMPLETED."""
    builder = ToolAgentBuilder(
        llm=FakeLLMWithLocalToolCall(),
        tools=[wait_tool],
        system_prompt="You are a workflow assistant.",
    )
    graph = builder.compile()
    result = await graph.ainvoke({"message": "Wait for 5 seconds"})
    assert result["transport_state"] == "COMPLETED"
    assert "wait completed" in result["formatted_response"]["content"].lower()
    assert len(result["tool_results"]) >= 1
