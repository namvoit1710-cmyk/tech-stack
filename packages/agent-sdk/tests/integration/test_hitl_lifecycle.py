from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import interrupt

from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentUseCase,
)
from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentInput,
    ResumeAgentUseCase,
)
from agent_sdk.layer2_application.interfaces.observability import ILogger, IMonitor
from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime
from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder


def _mock_logger():
    logger = MagicMock(spec=ILogger)
    return logger


def _mock_monitor():
    monitor = MagicMock(spec=IMonitor)
    monitor.track = MagicMock()
    return monitor


def _make_interrupting_tool():
    """A tool that always calls interrupt() with an AGENT_CALL payload."""

    @tool
    def call_sub_agent(input: str) -> str:
        """Calls a sub-agent via interrupt."""
        return interrupt(
            {
                "type": "AGENT_CALL",
                "agent_id": "sub-agent-1",
                "input": input,
                "message": "Calling sub-agent",
            }
        )

    return call_sub_agent


def _make_mock_llm_that_calls_tool(tool_name: str, tool_input: str):
    """Create a mock LLM that returns a tool_call on first invocation, then a text response."""
    call_count = 0

    async def ainvoke(messages):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc1", "name": tool_name, "args": {"input": tool_input}}
                ],
            )
            return msg
        else:
            return AIMessage(content="Task completed successfully")

    mock_llm = MagicMock()
    mock_llm.ainvoke = ainvoke
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)
    return mock_llm


def _build_graph_and_use_cases(tools, mock_llm, checkpointer):
    """Build a ToolAgent graph and wire it into ExecuteAgentUseCase + ResumeAgentUseCase."""
    builder = ToolAgentBuilder(tools=tools, llm=mock_llm)
    compiled_graph = builder.compile(checkpointer=checkpointer)

    execute_uc = ExecuteAgentUseCase(
        logger=_mock_logger(),
        monitor=_mock_monitor(),
        agent_graph=compiled_graph,
    )
    resume_uc = ResumeAgentUseCase(
        logger=_mock_logger(),
        monitor=_mock_monitor(),
        agent_runtime=LangGraphRuntime(compiled_graph),
    )
    return compiled_graph, execute_uc, resume_uc


@pytest.mark.asyncio
async def test_execute_interrupt_resume_cycle():
    """Full cycle: execute → interrupt → resume → success using MemorySaver."""
    checkpointer = MemorySaver()
    interrupt_tool = _make_interrupting_tool()
    mock_llm = _make_mock_llm_that_calls_tool("call_sub_agent", '{"message":"hello"}')

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    result = await execute_uc.execute(
        ExecuteAgentInput(
            message="run the workflow",
            conv_id="test-thread-1",
        )
    )
    assert result.interrupted is True
    assert result.interrupt_payload is not None
    thread_id = result.interrupt_payload.thread_id
    interrupt_id = result.interrupt_payload.interrupt_id

    resume_value = {
        "message": "done",
        "file_id": "abc-123",
        "content": "col1,col2\na,b",
    }
    resume_result = await resume_uc.execute(
        ResumeAgentInput(
            thread_id=thread_id,
            resume_value=resume_value,
            interrupt_id=interrupt_id,
        )
    )
    assert resume_result.status == "success"


@pytest.mark.asyncio
async def test_resume_preserves_message_history():
    """After resume, the graph continues with full message history intact."""
    checkpointer = MemorySaver()
    captured_messages = []

    interrupt_tool = _make_interrupting_tool()

    call_count = 0

    async def capturing_ainvoke(messages):
        nonlocal call_count
        call_count += 1
        captured_messages.append(list(messages))
        if call_count == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc1", "name": "call_sub_agent", "args": {"input": "test"}}
                ],
            )
        return AIMessage(content="final")

    mock_llm = MagicMock()
    mock_llm.ainvoke = capturing_ainvoke
    mock_llm.bind_tools = MagicMock(return_value=mock_llm)

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    result = await execute_uc.execute(
        ExecuteAgentInput(
            message="test",
            conv_id="test-thread-2",
        )
    )
    assert result.interrupted is True

    await resume_uc.execute(
        ResumeAgentInput(
            thread_id=result.interrupt_payload.thread_id,
            resume_value={"status": "ok"},
            interrupt_id=result.interrupt_payload.interrupt_id,
        )
    )

    assert len(captured_messages) >= 2, "LLM should have been called at least twice"
    assert len(captured_messages[1]) > len(captured_messages[0])


@pytest.mark.asyncio
async def test_resume_with_structured_business_data():
    """Resume value containing {file_id, content, message} is preserved verbatim in graph state."""
    checkpointer = MemorySaver()
    interrupt_tool = _make_interrupting_tool()
    mock_llm = _make_mock_llm_that_calls_tool("call_sub_agent", '{"message":"test"}')

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    result = await execute_uc.execute(
        ExecuteAgentInput(
            message="run",
            conv_id="test-thread-3",
        )
    )
    assert result.interrupted is True

    resume_value = {
        "message": "File Agent execute successfully.",
        "status": "success",
        "agent_result": {
            "file_id": "new-123",
            "content": "col1,col2\na,b",
            "message": "ok",
        },
    }
    resume_result = await resume_uc.execute(
        ResumeAgentInput(
            thread_id=result.interrupt_payload.thread_id,
            resume_value=resume_value,
            interrupt_id=result.interrupt_payload.interrupt_id,
        )
    )

    assert resume_result.status == "success"
