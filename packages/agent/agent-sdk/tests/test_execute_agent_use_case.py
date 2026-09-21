import importlib
import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer1_domain.entities.agent_request import RequestContext
from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
    ExecuteAgentInput,
    ExecuteAgentOutput,
    ExecuteAgentUseCase,
)
from agent_sdk.layer2_application.features.get_agent_info.use_cases.get_agent_info_use_case import (
    GetAgentInfoUseCase,
)
from tests.helpers.testing import StubLogger, StubMonitor


@pytest.mark.asyncio
async def test_execute_with_graph():
    formatted_state = {
        "formatted_response": {"content": "Hello from agent", "status": "success"}
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Hello",
        conv_id="conv-1",
        user_id="user-1",
        tenant_id="tenant-1",
    )
    result = await use_case.execute(request)
    mock_graph.ainvoke.assert_called_once()
    call_args = mock_graph.ainvoke.call_args[0][0]
    assert call_args["message"] == "Hello"
    assert call_args["conv_id"] == "conv-1"
    assert isinstance(result, ExecuteAgentOutput)
    assert result.message == "Hello from agent"
    assert result.error is None


@pytest.mark.asyncio
async def test_execute_without_graph_echoes():
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=None
    )
    request = ExecuteAgentInput(message="Echo this", conv_id="conv-2")
    result = await use_case.execute(request)
    assert isinstance(result, ExecuteAgentOutput)
    assert result.message != ""
    assert result.error is None


@pytest.mark.asyncio
async def test_execute_handles_exception():
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("Graph exploded"))
    logger = StubLogger()
    use_case = ExecuteAgentUseCase(
        logger=logger, monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(message="Trigger error", conv_id="conv-3")
    result = await use_case.execute(request)
    assert isinstance(result, ExecuteAgentOutput)
    assert result.error is not None
    assert "Graph exploded" in result.error
    assert result.message == ""


def test_get_agent_info_returns_metadata():
    from agent_sdk.layer1_domain.entities.agent_info import AgentInfo

    class _StubSettings:
        AGENT_TYPE = "test-agent"
        AGENT_VERSION = "1.0.0"
        SDK_VERSION = "2.0.0"
        AGENT_DOMAIN = "testing"
        CAPABILITIES = []
        DEFAULT_TENANT_ID = "default"

    use_case = GetAgentInfoUseCase(logger=StubLogger(), settings=_StubSettings())
    result = use_case.execute()
    assert isinstance(result, AgentInfo)
    assert result.agent_type != ""
    assert result.version != ""
    assert result.sdk_version != ""


def test_execute_is_async():
    """execute() must be a coroutine function (async def)."""
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=None
    )
    assert inspect.iscoroutinefunction(
        use_case.execute
    ), "ExecuteAgentUseCase.execute must be declared as 'async def'"


@pytest.mark.asyncio
async def test_execute_uses_ainvoke_on_graph():
    """execute() must call graph.ainvoke, not graph.invoke."""
    formatted_state = {
        "formatted_response": {"content": "Async result", "status": "success"}
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(message="Async hello", conv_id="conv-async")
    result = await use_case.execute(request)
    mock_graph.ainvoke.assert_called_once()
    call_args = mock_graph.ainvoke.call_args[0][0]
    assert call_args["message"] == "Async hello"
    assert call_args["conv_id"] == "conv-async"
    assert isinstance(result, ExecuteAgentOutput)
    assert result.message == "Async result"
    assert result.error is None


@pytest.mark.asyncio
async def test_execute_async_without_graph_echoes():
    """execute() without graph must return echo response asynchronously."""
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=None
    )
    request = ExecuteAgentInput(message="Echo async", conv_id="conv-echo-async")
    result = await use_case.execute(request)
    assert isinstance(result, ExecuteAgentOutput)
    assert result.message != ""
    assert result.error is None


@pytest.mark.asyncio
async def test_execute_async_handles_exception():
    """execute() must handle ainvoke exceptions and return error output."""
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("Async graph exploded"))
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(message="Trigger async error", conv_id="conv-err-async")
    result = await use_case.execute(request)
    assert isinstance(result, ExecuteAgentOutput)
    assert result.error is not None
    assert "Async graph exploded" in result.error
    assert result.message == ""


@pytest.mark.asyncio
async def test_execute_via_graph_maps_context_into_initial_state():
    """_execute_via_graph must include request.context in initial_state."""
    captured_state = {}

    async def capture_ainvoke(state, config=None):
        captured_state.update(state)
        return {"formatted_response": {"content": "ok", "status": "success"}}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=capture_ainvoke)
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    context = RequestContext(agent="my-agent", source="web", history=["hi", "hello"])
    request = ExecuteAgentInput(
        message="Test context",
        conv_id="conv-ctx",
        context=context,
    )
    await use_case.execute(request)
    assert "context" in captured_state, "initial_state must include 'context' key"
    from dataclasses import asdict

    assert captured_state["context"] == asdict(context)


@pytest.mark.asyncio
async def test_execute_via_graph_context_none_when_not_provided():
    """_execute_via_graph must include context=None in initial_state when not provided."""
    captured_state = {}

    async def capture_ainvoke(state, config=None):
        captured_state.update(state)
        return {"formatted_response": {"content": "ok", "status": "success"}}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=capture_ainvoke)
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(message="No context", conv_id="conv-noctx")
    await use_case.execute(request)
    assert "context" in captured_state
    assert captured_state["context"] is None


@pytest.mark.asyncio
async def test_execute_via_graph_includes_session_id_in_initial_state():
    """_execute_via_graph must include session_id in initial_state."""
    captured_state = {}

    async def capture_ainvoke(state, config=None):
        captured_state.update(state)
        return {"formatted_response": {"content": "ok", "status": "success"}}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=capture_ainvoke)
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Test session", conv_id="conv-sess", session_id="my-session-123"
    )
    await use_case.execute(request)
    assert "session_id" in captured_state
    assert captured_state["session_id"] == "my-session-123"


@pytest.mark.asyncio
async def test_execute_via_graph_generates_session_id_when_missing():
    """_execute_via_graph must generate a UUID session_id when request.session_id is empty."""
    captured_state = {}

    async def capture_ainvoke(state, config=None):
        captured_state.update(state)
        return {"formatted_response": {"content": "ok", "status": "success"}}

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=capture_ainvoke)
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(), monitor=StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(message="Gen session", conv_id="conv-gen")
    await use_case.execute(request)
    assert "session_id" in captured_state
    session_id = captured_state["session_id"]
    assert session_id, "session_id must not be empty"
    uuid.UUID(session_id)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow lifecycle events are NOT auto-published by ExecuteAgentUseCase.
# The SDK provides the mechanism (emit_workflow_event, event classes) but
# agents decide when to emit lifecycle events (business logic, not framework).
# ─────────────────────────────────────────────────────────────────────────────

_LIFECYCLE_EVENT_TYPES = {"WORKFLOW_STARTED", "WORKFLOW_COMPLETED", "WORKFLOW_FAILED"}


def _make_emitter():
    mock_publisher = AsyncMock()
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    return WorkflowEventEmitter(publisher=mock_publisher), mock_publisher


@pytest.mark.asyncio
async def test_execute_does_not_auto_emit_workflow_started():
    """ExecuteAgentUseCase must NOT auto-emit WORKFLOW_STARTED — that is business logic."""
    emitter, mock_publisher = _make_emitter()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={"formatted_response": {"content": "hi", "status": "success"}}
    )
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=mock_graph,
        workflow_event_emitter=emitter,
    )
    await use_case.execute(ExecuteAgentInput(message="hi", conv_id="conv-ev-1"))
    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_execute_does_not_auto_emit_workflow_completed():
    """ExecuteAgentUseCase must NOT auto-emit WORKFLOW_COMPLETED — that is business logic."""
    emitter, mock_publisher = _make_emitter()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={"formatted_response": {"content": "done", "status": "success"}}
    )
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=mock_graph,
        workflow_event_emitter=emitter,
    )
    await use_case.execute(ExecuteAgentInput(message="hi", conv_id="conv-ev-2"))
    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_execute_does_not_auto_emit_workflow_failed():
    """ExecuteAgentUseCase must NOT auto-emit WORKFLOW_FAILED — that is business logic."""
    emitter, mock_publisher = _make_emitter()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=mock_graph,
        workflow_event_emitter=emitter,
    )
    result = await use_case.execute(
        ExecuteAgentInput(message="hi", conv_id="conv-ev-3")
    )
    assert result.status == "error"
    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_execute_does_not_auto_emit_on_echo_path():
    """ExecuteAgentUseCase must NOT auto-emit lifecycle events on echo (no graph) path."""
    emitter, mock_publisher = _make_emitter()
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=None,
        workflow_event_emitter=emitter,
    )
    result = await use_case.execute(
        ExecuteAgentInput(message="echo me", conv_id="conv-echo")
    )
    assert "[echo]" in result.message
    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_execute_without_emitter_still_works():
    """ExecuteAgentUseCase must work normally when no workflow_event_emitter given."""
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={"formatted_response": {"content": "ok", "status": "success"}}
    )
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=mock_graph,
    )
    result = await use_case.execute(
        ExecuteAgentInput(message="hi", conv_id="conv-noemit")
    )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_execute_emitter_failure_does_not_break_execution():
    """Emitter publish failure must not propagate to the caller."""
    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = Exception("broker down")
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    emitter = WorkflowEventEmitter(publisher=mock_publisher, logger=StubLogger())
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={"formatted_response": {"content": "ok", "status": "success"}}
    )
    use_case = ExecuteAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_graph=mock_graph,
        workflow_event_emitter=emitter,
    )
    result = await use_case.execute(
        ExecuteAgentInput(message="hi", conv_id="conv-fail")
    )
    assert result.status == "success"


# ─────────────────────────────────────────────────────────────────────────────
# Layer boundary enforcement: use case must NOT import Layer 4 or LangGraph
# ─────────────────────────────────────────────────────────────────────────────


def test_execute_agent_use_case_does_not_import_layer4_settings():
    """ExecuteAgentUseCase module must not import Layer 4 settings at module level."""
    import sys

    mod_name = "agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case"
    mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
    source = inspect.getsource(mod)
    assert (
        "from agent_sdk.layer4_frameworks" not in source
    ), "execute_agent_use_case.py must not contain 'from agent_sdk.layer4_frameworks' imports"


def test_execute_agent_use_case_does_not_import_langgraph():
    """ExecuteAgentUseCase module must not import langgraph directly."""
    import sys

    mod_name = "agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case"
    mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
    source = inspect.getsource(mod)
    assert (
        "from langgraph" not in source
    ), "execute_agent_use_case.py must not contain 'from langgraph' imports"
    assert (
        "import langgraph" not in source
    ), "execute_agent_use_case.py must not contain 'import langgraph' imports"


def test_get_agent_info_use_case_does_not_have_fallback_layer4_import():
    """GetAgentInfoUseCase module must not import Layer 4 settings at module level."""
    import sys

    mod_name = "agent_sdk.layer2_application.features.get_agent_info.use_cases.get_agent_info_use_case"
    mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
    source = inspect.getsource(mod)
    assert (
        "from agent_sdk.layer4_frameworks" not in source
    ), "get_agent_info_use_case.py must not contain 'from agent_sdk.layer4_frameworks' imports"
