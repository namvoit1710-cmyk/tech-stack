"""Regression coverage for workflow-agent style custom graphs without SDK middleware.

Verifies that the SDK correctly handles terminal output from graphs that:
 - Only set `agent_result` (no `formatted_response` / format_response_node middleware)
 - Use `agent_data` + `message` + `status` as terminal output
 - Are used as the parent graph in AgentCallCoordinator scenarios

No workflow-builder or workflow-supervisor app code is imported here.
No external HTTP services are contacted.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
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
from agent_sdk.layer4_frameworks.http.agent_call_coordinator import AgentCallCoordinator


def _mock_logger():
    logger = MagicMock(spec=ILogger)
    return logger


def _mock_monitor():
    monitor = MagicMock(spec=IMonitor)
    monitor.track = MagicMock()
    return monitor


# ---------------------------------------------------------------------------
# Custom graph fixture helpers
# ---------------------------------------------------------------------------


def _build_agent_result_only_graph(checkpointer=None):
    """Build a compiled graph that sets agent_result only — no format_response_node middleware."""

    def work_node(state: dict) -> dict:
        return {
            "agent_result": {
                "content": "processed result",
                "file_id": "file-abc-123",
                "status": "success",
            }
        }

    graph = StateGraph(dict)
    graph.add_node("work", work_node)
    graph.set_entry_point("work")
    graph.add_edge("work", END)
    return graph.compile(checkpointer=checkpointer)


def _build_agent_result_with_interrupt_graph(checkpointer=None):
    """Build a compiled graph that raises an AGENT_CALL interrupt before producing agent_result."""

    def trigger_agent_call(state: dict) -> dict:
        resume_value = interrupt(
            {
                "type": "AGENT_CALL",
                "agent_id": "downstream-agent",
                "input": {"query": state.get("message", "")},
                "message": "Calling downstream agent",
            }
        )
        return {"agent_call_result": resume_value}

    def finalize(state: dict) -> dict:
        sub_result = state.get("agent_call_result", {})
        return {
            "agent_result": {
                "content": sub_result.get("message", "done"),
                "downstream_data": sub_result.get("agent_result", {}),
                "status": "success",
            }
        }

    graph = StateGraph(dict)
    graph.add_node("trigger_agent_call", trigger_agent_call)
    graph.add_node("finalize", finalize)
    graph.set_entry_point("trigger_agent_call")
    graph.add_edge("trigger_agent_call", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)


def _build_use_cases(compiled_graph):
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
    return execute_uc, resume_uc


# ---------------------------------------------------------------------------
# Step 2: execute test — custom graph with agent_result only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_custom_graph_agent_result_only_exposes_agent_data():
    """ExecuteAgentUseCase must expose agent_result content in agent_data for graphs
    that do not use format_response_node middleware.

    A custom workflow-agent style graph that only sets agent_result should produce
    a meaningful status, message, and agent_data on the SDK output — not empty success.
    """
    compiled = _build_agent_result_only_graph()
    execute_uc, _ = _build_use_cases(compiled)

    result = await execute_uc.execute(
        ExecuteAgentInput(message="process", conv_id="exec-t1")
    )

    assert (
        result.status == "success"
    ), f"Expected status='success', got {result.status!r}"
    assert result.agent_data, "agent_data must be populated from agent_result for custom graphs without middleware"
    assert (
        result.agent_data.get("file_id") == "file-abc-123"
    ), f"Expected file_id='file-abc-123' in agent_data, got {result.agent_data!r}"
    assert (
        result.message == "processed result"
    ), f"Expected message='processed result', got {result.message!r}"


# ---------------------------------------------------------------------------
# Step 2: resume test — custom graph with agent_result only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_custom_graph_agent_result_only_exposes_agent_data():
    """ResumeAgentUseCase must expose agent_result content in agent_data after resume
    for graphs that do not use format_response_node middleware.

    Resume cycle: execute → interrupted → resume → terminal output must have
    meaningful agent_data drawn from agent_result.
    """
    checkpointer = MemorySaver()
    compiled = _build_agent_result_with_interrupt_graph(checkpointer=checkpointer)
    execute_uc, resume_uc = _build_use_cases(compiled)

    thread_id = "resume-custom-t1"
    exec_result = await execute_uc.execute(
        ExecuteAgentInput(message="start process", conv_id=thread_id)
    )

    assert (
        exec_result.interrupted is True
    ), "Graph should be interrupted on first execute"
    assert exec_result.interrupt_payload is not None

    resume_value = {
        "message": "Downstream done",
        "status": "success",
        "agent_result": {"output_key": "val-xyz"},
    }

    resume_result = await resume_uc.execute(
        ResumeAgentInput(
            thread_id=thread_id,
            resume_value=resume_value,
            interrupt_id=exec_result.interrupt_payload.interrupt_id,
        )
    )

    assert (
        resume_result.status == "success"
    ), f"Expected status='success' after resume, got {resume_result.status!r}"
    assert (
        resume_result.agent_data
    ), "agent_data must be populated from agent_result after resume for custom graphs"
    assert (
        resume_result.agent_data.get("content") == "Downstream done"
    ), f"Expected content='Downstream done' in agent_data, got {resume_result.agent_data!r}"


# ---------------------------------------------------------------------------
# Step 3: coordinator transport error → terminal error on custom graph
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_graph_coordinator_transport_error_yields_terminal_error():
    """When a coordinator-owned transport error occurs during sub-agent call in a
    custom graph (no middleware), the coordinator must return a terminal status='error'
    output immediately, not feed the error dict into resume.

    This is the same guarantee as for tool-agent graphs, now validated for pure
    custom compiled graphs.
    """
    checkpointer = MemorySaver()
    compiled = _build_agent_result_with_interrupt_graph(checkpointer=checkpointer)
    execute_uc, resume_uc = _build_use_cases(compiled)

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://bad-host:9999")

    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.post = AsyncMock(side_effect=Exception("connection refused"))

    coordinator = AgentCallCoordinator(
        endpoint_resolver=mock_resolver,
        resume_use_case=resume_uc,
        http_client=mock_http,
    )

    final_result = await coordinator.execute_with_auto_resume(
        execute_uc,
        ExecuteAgentInput(
            message="trigger sub-agent", conv_id="coord-custom-transport-1"
        ),
    )

    assert final_result.status == "error", (
        "Coordinator must return terminal error when sub-agent transport fails, "
        f"even for custom graphs. Got status={final_result.status!r}"
    )
    assert (
        final_result.error is not None
    ), "Coordinator must populate error field on terminal transport error output"


# ---------------------------------------------------------------------------
# Step 4: business-error payload without sentinel → resume still visible
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_graph_business_error_payload_without_sentinel_passes_to_resume():
    """Sub-agent business error payloads (without _coordinator_error sentinel) must be
    passed verbatim to resume on the custom graph.

    This distinguishes between coordinator-owned errors (fail-fast) and genuine
    sub-agent domain errors that the parent graph should receive and handle.
    """
    checkpointer = MemorySaver()
    compiled = _build_agent_result_with_interrupt_graph(checkpointer=checkpointer)
    execute_uc, resume_uc = _build_use_cases(compiled)

    mock_resolver = AsyncMock()
    mock_resolver.resolve_endpoint = AsyncMock(return_value="http://sub-agent:8080")

    business_error_payload = {
        "message": "field validation failed",
        "status": "error",
        "success": False,
        "error": "Required field 'target' missing",
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
        ExecuteAgentInput(message="process data", conv_id="coord-custom-biz-error-1"),
    )

    assert len(resume_calls) == 1, (
        "Business error payloads (without coordinator sentinel) must be passed to resume. "
        f"Resume was called {len(resume_calls)} time(s)"
    )
    assert resume_calls[0].resume_value == business_error_payload, (
        "Business error payload must be passed verbatim to resume. "
        f"Got resume_value={resume_calls[0].resume_value!r}"
    )


# ---------------------------------------------------------------------------
# Step 5: middleware-based tool agent test still passes unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_tool_agent_still_works_in_same_run():
    """Verify that the standard middleware-based ToolAgentBuilder graph continues to
    work correctly in the same test run as the custom graph tests.

    The SDK must be backward-compatible: adding support for agent_result-only graphs
    must not break existing format_response_node (formatted_response) graphs.
    """
    from langchain_core.messages import AIMessage

    from agent_sdk.layer4_frameworks.graph.tool_agent_builder import ToolAgentBuilder

    call_count = 0

    class _FakeLLM:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            nonlocal call_count
            call_count += 1
            return AIMessage(content="Task completed by tool agent")

    fake_llm = _FakeLLM()
    builder = ToolAgentBuilder(tools=[], llm=fake_llm)
    compiled = builder.compile()
    execute_uc, _ = _build_use_cases(compiled)

    result = await execute_uc.execute(
        ExecuteAgentInput(message="run tool agent", conv_id="compat-tool-t1")
    )

    assert (
        result.status == "success"
    ), f"Middleware-based tool agent must still return status='success', got {result.status!r}"
    assert (
        result.message == "Task completed by tool agent"
    ), f"Expected 'Task completed by tool agent' in message, got {result.message!r}"
