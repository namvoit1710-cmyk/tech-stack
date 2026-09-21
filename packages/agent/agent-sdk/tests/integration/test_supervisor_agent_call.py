from unittest.mock import AsyncMock, MagicMock

import httpx
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
    ResumeAgentUseCase,
)
from agent_sdk.layer2_application.interfaces.observability import ILogger, IMonitor
from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime
from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder
from agent_sdk.layer4_frameworks.http.agent_call_coordinator import (
    AgentCallCoordinator,
)


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
async def test_coordinator_auto_resume_agent_call():
    """Full integration: coordinator detects AGENT_CALL, calls mock sub-agent, resumes."""
    checkpointer = MemorySaver()
    interrupt_tool = _make_interrupting_tool()
    mock_llm = _make_mock_llm_that_calls_tool("call_sub_agent", '{"message":"hello"}')

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(
        return_value="http://mock-sub-agent:8080"
    )

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    sub_response = MagicMock()
    sub_response.json.return_value = {
        "message": "File Agent execute successfully.",
        "status": "success",
        "agent_result": {"file_id": "new-456", "content": "data-here"},
    }
    sub_response.raise_for_status = MagicMock()
    mock_http.post = AsyncMock(return_value=sub_response)

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=resume_uc,
        http_client=mock_http,
    )

    final_result = await coordinator.execute_with_auto_resume(
        execute_uc,
        ExecuteAgentInput(message="do the thing", conv_id="coord-thread-1"),
    )

    mock_http.post.assert_called_once()
    call_url = mock_http.post.call_args[0][0]
    assert "mock-sub-agent:8080" in call_url
    assert "/api/v1/execute" in call_url

    assert final_result.status == "success"
    assert final_result.interrupted is not True


@pytest.mark.asyncio
async def test_coordinator_passes_structured_response_verbatim():
    """Sub-agent structured business data (file_id, content) flows through without modification."""
    checkpointer = MemorySaver()
    interrupt_tool = _make_interrupting_tool()
    mock_llm = _make_mock_llm_that_calls_tool("call_sub_agent", '{"message":"process"}')

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://file-agent:8080")

    critical_payload = {
        "message": "File Agent execute successfully.",
        "status": "success",
        "agent_result": {
            "file_id": "storage-key-789",
            "content": "col1,col2\na,b\nc,d",
            "message": "ok",
        },
    }

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    sub_response = MagicMock()
    sub_response.json.return_value = critical_payload
    sub_response.raise_for_status = MagicMock()
    mock_http.post = AsyncMock(return_value=sub_response)

    resume_calls = []
    original_resume = resume_uc.execute

    async def capturing_resume(req):
        resume_calls.append(req)
        return await original_resume(req)

    resume_uc.execute = capturing_resume

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=resume_uc,
        http_client=mock_http,
    )

    await coordinator.execute_with_auto_resume(
        execute_uc,
        ExecuteAgentInput(message="process file", conv_id="coord-thread-2"),
    )

    assert len(resume_calls) == 1
    assert resume_calls[0].resume_value == critical_payload
    assert resume_calls[0].resume_value["agent_result"]["file_id"] == "storage-key-789"
    assert (
        resume_calls[0].resume_value["agent_result"]["content"] == "col1,col2\na,b\nc,d"
    )


@pytest.mark.asyncio
async def test_coordinator_returns_terminal_error_for_coordinator_owned_errors():
    """When _call_sub_agent produces a coordinator-owned error (sentinel _coordinator_error=True),
    the coordinator must return a terminal error output immediately instead of blindly resuming
    the graph with the error dict.

    Coordinator-owned errors (transport failures, validation errors) are NOT sub-agent business
    responses and should not be fed back into the graph as resume values.
    """
    checkpointer = MemorySaver()
    interrupt_tool = _make_interrupting_tool()
    mock_llm = _make_mock_llm_that_calls_tool("call_sub_agent", '{"message":"run"}')

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://bad-agent:8080")

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=Exception("bad endpoint"))

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=resume_uc,
        http_client=mock_http,
    )

    final_result = await coordinator.execute_with_auto_resume(
        execute_uc,
        ExecuteAgentInput(message="trigger sub-agent call", conv_id="coord-sentinel-1"),
    )

    assert final_result.status == "error", (
        "Coordinator must return status='error' when sub-agent call fails due to transport error. "
        f"Got status={final_result.status!r}"
    )
    assert (
        final_result.error is not None
    ), "Coordinator must set error message on terminal error output"


@pytest.mark.asyncio
async def test_coordinator_passes_through_genuine_business_error_without_sentinel():
    """Sub-agent business payloads that include success=False and status='error' but do NOT
    carry the coordinator sentinel must pass through resume unchanged.

    This ensures coordinator does not intercept real agent-domain errors like
    {"success": False, "status": "error", "error": "validation failed"} from an agent
    that intentionally returns an error response as its terminal output.
    """
    checkpointer = MemorySaver()
    interrupt_tool = _make_interrupting_tool()
    mock_llm = _make_mock_llm_that_calls_tool("call_sub_agent", '{"message":"process"}')

    _, execute_uc, resume_uc = _build_graph_and_use_cases(
        tools=[interrupt_tool],
        mock_llm=mock_llm,
        checkpointer=checkpointer,
    )

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    business_error_payload = {
        "message": "validation failed",
        "status": "error",
        "success": False,
        "error": "Required field missing",
        "error_code": "VALIDATION_ERROR",
    }

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    sub_response = MagicMock()
    sub_response.json.return_value = business_error_payload
    sub_response.raise_for_status = MagicMock()
    mock_http.post = AsyncMock(return_value=sub_response)

    resume_calls = []
    original_resume = resume_uc.execute

    async def capturing_resume(req):
        resume_calls.append(req)
        return await original_resume(req)

    resume_uc.execute = capturing_resume

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=resume_uc,
        http_client=mock_http,
    )

    await coordinator.execute_with_auto_resume(
        execute_uc,
        ExecuteAgentInput(
            message="process with error sub-agent", conv_id="coord-biz-error-1"
        ),
    )

    assert len(resume_calls) == 1, (
        "Coordinator must still resume graph with genuine sub-agent business error payloads. "
        f"Resume was called {len(resume_calls)} time(s)"
    )
    assert resume_calls[0].resume_value == business_error_payload, (
        "Business error payload (without coordinator sentinel) must be passed verbatim to resume. "
        f"Got resume_value={resume_calls[0].resume_value!r}"
    )
