from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_agent_graph_builder_does_not_wrap_runtime_interrupt_with_deps():
    """LangGraph interrupt() must remain HITL control flow, not AGENT_GRAPH_ERROR."""
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt as lg_interrupt

    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )
    from agent_sdk.layer4_frameworks.graph.agent_graph_builder import AgentGraphBuilder
    from tests.helpers.testing import StubLogger, StubMonitor

    def ask_for_confirmation(state: dict, deps: dict) -> dict:
        answer = lg_interrupt({"type": "CONFIRMATION", "message": "Approve?"})
        return {"confirmed": answer}

    builder = AgentGraphBuilder(deps={"service": "available"})
    builder.add_node("ask", ask_for_confirmation)
    builder.set_entry_point("ask")
    graph = builder.compile(checkpointer=MemorySaver())

    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=graph,
    )

    result = await use_case.execute(
        ExecuteAgentInput(
            message="go",
            conv_id="echo-hitl-1",
            user_id="u1",
            tenant_id="t1",
        )
    )

    assert result.status == "interrupted"
    assert result.interrupted is True
    assert result.error is None
    assert result.interrupt_payload is not None
    assert result.interrupt_payload.thread_id == "echo-hitl-1"
    assert result.interrupt_payload.value["type"] == "CONFIRMATION"
