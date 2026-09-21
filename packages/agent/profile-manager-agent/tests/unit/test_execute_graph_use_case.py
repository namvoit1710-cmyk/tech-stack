"""Unit specs for ExecuteGraphUseCase (layer2 application — prime unit target).

Target: app/layer2_application/use_cases/execute_graph_use_case.py

Collaborator: `graph` (its `ainvoke` is async -> AsyncMock). Grounded facts:
- conv_id: uses command.conv_id, else a fresh uuid4.
- token is placed in config["configurable"]["token"], NOT in state.
- returns final_state.get("formatted_response", {}).
"""
import uuid
import pytest
from unittest.mock import AsyncMock

from app.layer2_application.features.execute_agent.use_cases.execute_graph_use_case import (
    ExecuteGraphCommand,
    ExecuteGraphUseCase,
)


def _command(**overrides):
    base = dict(
        message="find users",
        user_id="u1",
        tenant_id="t1",
        source="web",
        parameters={"k": "v"},
        token="secret-token",
        conv_id="conv-1",
    )
    base.update(overrides)
    return ExecuteGraphCommand(**base)


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    graph.ainvoke.return_value = {"formatted_response": {"status": "success"}}
    return graph


# happy path: state assembled, formatted_response returned
@pytest.mark.asyncio
async def test_execute_returns_formatted_response(mock_graph):
    uc = ExecuteGraphUseCase(mock_graph)

    out = await uc.execute(_command())

    assert out == {"status": "success"}
    mock_graph.ainvoke.assert_awaited_once()


# happy path: full state field mapping + IDLE transport + token kept out of state
@pytest.mark.asyncio
async def test_execute_maps_all_fields_into_state(mock_graph):
    uc = ExecuteGraphUseCase(mock_graph)

    await uc.execute(_command())

    state = mock_graph.ainvoke.call_args[0][0]
    assert state["message"] == "find users"
    assert state["conv_id"] == "conv-1"
    assert state["user_id"] == "u1"
    assert state["tenant_id"] == "t1"
    assert state["source"] == "web"
    assert state["parameters"] == {"k": "v"}
    assert state["transport_state"] == "IDLE"
    assert "token" not in state


# happy path: token routed into configurable, thread_id == conv_id
@pytest.mark.asyncio
async def test_execute_places_token_in_config_not_state(mock_graph):
    uc = ExecuteGraphUseCase(mock_graph)

    await uc.execute(_command(conv_id="thread-9", token="tk"))

    config = mock_graph.ainvoke.call_args.kwargs["config"]
    assert config["configurable"]["token"] == "tk"
    assert config["configurable"]["thread_id"] == "thread-9"


# edge: empty conv_id -> a uuid4 is generated and reused as thread_id
@pytest.mark.asyncio
async def test_execute_generates_conv_id_when_empty(mock_graph):
    uc = ExecuteGraphUseCase(mock_graph)

    await uc.execute(_command(conv_id=""))

    state = mock_graph.ainvoke.call_args[0][0]
    config = mock_graph.ainvoke.call_args.kwargs["config"]
    assert state["conv_id"] != ""
    # a valid uuid4
    uuid.UUID(state["conv_id"])
    assert state["conv_id"] == config["configurable"]["thread_id"]


# boundary / edge: token is None (allowed by the dataclass type) -> flows through
@pytest.mark.asyncio
async def test_execute_allows_none_token(mock_graph):
    uc = ExecuteGraphUseCase(mock_graph)

    await uc.execute(_command(token=None))

    config = mock_graph.ainvoke.call_args.kwargs["config"]
    assert config["configurable"]["token"] is None


# boundary: graph returns state with no formatted_response -> default {}
@pytest.mark.asyncio
async def test_execute_returns_empty_dict_when_no_formatted_response(mock_graph):
    mock_graph.ainvoke.return_value = {}
    uc = ExecuteGraphUseCase(mock_graph)

    out = await uc.execute(_command())

    assert out == {}


# invalid input: empty parameters dict is accepted verbatim
@pytest.mark.asyncio
async def test_execute_passes_empty_parameters(mock_graph):
    uc = ExecuteGraphUseCase(mock_graph)

    await uc.execute(_command(parameters={}))

    assert mock_graph.ainvoke.call_args[0][0]["parameters"] == {}


# failure path: graph raises -> propagates (use-case does not swallow)
@pytest.mark.asyncio
async def test_execute_propagates_graph_error(mock_graph):
    mock_graph.ainvoke.side_effect = RuntimeError("graph exploded")
    uc = ExecuteGraphUseCase(mock_graph)

    with pytest.raises(RuntimeError, match="graph exploded"):
        await uc.execute(_command())
