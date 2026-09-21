"""Tests for session_id consistency and GraphInterrupt ExceptionGroup handling.

Covers:
- session_id generated once and used consistently in both output and graph initial_state
- GraphInterrupt wrapped in ExceptionGroup is still handled as interrupted status
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentUseCase,
)


def _make_use_case(graph):
    logger = MagicMock()
    logger.info = MagicMock()
    logger.error = MagicMock()
    logger.warning = MagicMock()

    monitor = MagicMock()
    monitor.track = MagicMock()

    return ExecuteAgentUseCase(logger=logger, monitor=monitor, agent_graph=graph)


@pytest.mark.asyncio
async def test_session_id_consistent_between_output_and_graph_state():
    """When no session_id provided, the generated UUID must be the same
    in the returned output AND in the graph's initial_state."""
    captured_state = {}

    async def capture_ainvoke(state, config=None):
        captured_state.update(state)
        return {"message": "done", "transport_state": "COMPLETED"}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=capture_ainvoke)

    use_case = _make_use_case(mock_graph)
    request = ExecuteAgentInput(message="test", session_id="")

    result = await use_case.execute(request)

    assert (
        result.session_id == captured_state["session_id"]
    ), f"Output session_id={result.session_id} != graph state session_id={captured_state['session_id']}"
    uuid.UUID(result.session_id)


@pytest.mark.asyncio
async def test_session_id_provided_is_used_consistently():
    """When a session_id is provided, it must appear in both output and graph initial_state."""
    captured_state = {}
    provided_id = "fixed-session-id-123"

    async def capture_ainvoke(state, config=None):
        captured_state.update(state)
        return {"message": "done", "transport_state": "COMPLETED"}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=capture_ainvoke)

    use_case = _make_use_case(mock_graph)
    request = ExecuteAgentInput(message="test", session_id=provided_id)

    result = await use_case.execute(request)

    assert result.session_id == provided_id
    assert captured_state["session_id"] == provided_id


@pytest.mark.asyncio
async def test_graph_interrupt_exception_group_handled():
    """GraphInterrupt wrapped in ExceptionGroup should still return interrupted status."""
    from langgraph.errors import GraphInterrupt
    from langgraph.types import Interrupt

    mock_graph = MagicMock()
    interrupt_obj = Interrupt(
        value={"type": "AGENT_CALL", "agent_id": "test"}, id="int-1"
    )
    interrupt = GraphInterrupt([interrupt_obj])
    mock_graph.ainvoke = AsyncMock(side_effect=BaseExceptionGroup("", [interrupt]))

    use_case = _make_use_case(mock_graph)
    result = await use_case.execute(ExecuteAgentInput(message="test"))

    assert result.status == "interrupted"
    assert result.interrupted is True
