"""
Tests that ResumeAgentUseCase does NOT auto-publish workflow lifecycle events.

Workflow lifecycle events (WORKFLOW_STARTED, WORKFLOW_COMPLETED, WORKFLOW_FAILED)
are business logic — agents decide when to emit them manually via
emit_workflow_event(). The SDK provides the mechanism but does not force them.

Graph-level observability events (NodeStarted, NodeCompleted, ToolSelected)
are still auto-emitted by AgentGraphBuilder and ToolAgentBuilder.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime
from tests.helpers.testing import StubLogger, StubMonitor

_LIFECYCLE_EVENT_TYPES = {"WORKFLOW_STARTED", "WORKFLOW_COMPLETED", "WORKFLOW_FAILED"}


def _make_emitter():
    mock_publisher = AsyncMock()
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    return WorkflowEventEmitter(publisher=mock_publisher), mock_publisher


@pytest.mark.asyncio
async def test_resume_does_not_auto_emit_workflow_started():
    """ResumeAgentUseCase must NOT auto-emit WORKFLOW_STARTED — that is business logic."""
    emitter, mock_publisher = _make_emitter()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"message": "resumed"})

    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    use_case = ResumeAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
        workflow_event_emitter=emitter,
    )
    request = ResumeAgentInput(thread_id="thread-1", resume_value="yes")
    await use_case.execute(request)

    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_resume_does_not_auto_emit_workflow_completed():
    """ResumeAgentUseCase must NOT auto-emit WORKFLOW_COMPLETED — that is business logic."""
    emitter, mock_publisher = _make_emitter()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"message": "done"})

    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    use_case = ResumeAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
        workflow_event_emitter=emitter,
    )
    await use_case.execute(ResumeAgentInput(thread_id="t-2", resume_value="ok"))

    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_resume_does_not_auto_emit_workflow_failed():
    """ResumeAgentUseCase must NOT auto-emit WORKFLOW_FAILED — that is business logic."""
    emitter, mock_publisher = _make_emitter()
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("resume boom"))

    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    use_case = ResumeAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
        workflow_event_emitter=emitter,
    )
    result = await use_case.execute(ResumeAgentInput(thread_id="t-3", resume_value="x"))
    assert result.status == "error"

    published_types = {
        call[0][1].get("event_type") for call in mock_publisher.publish.call_args_list
    }
    assert not published_types & _LIFECYCLE_EVENT_TYPES


@pytest.mark.asyncio
async def test_resume_without_emitter_still_works():
    """ResumeAgentUseCase must work normally when no workflow_event_emitter given."""
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"message": "ok"})

    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    use_case = ResumeAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t-4", resume_value="ok")
    )
    assert result.status == "success"


@pytest.mark.asyncio
async def test_resume_emitter_failure_does_not_break_execution():
    """Emitter publish failures must not propagate to the caller."""
    mock_publisher = AsyncMock()
    mock_publisher.publish.side_effect = Exception("broker down")
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    emitter = WorkflowEventEmitter(publisher=mock_publisher, logger=StubLogger())

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value={"message": "ok"})

    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    use_case = ResumeAgentUseCase(
        logger=StubLogger(),
        monitor=StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
        workflow_event_emitter=emitter,
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t-5", resume_value="ok")
    )
    assert result.status == "success"


# ─────────────────────────────────────────────────────────────────────────────
# Layer boundary enforcement: use case must NOT import LangGraph directly
# ─────────────────────────────────────────────────────────────────────────────


def test_resume_agent_use_case_does_not_import_langgraph():
    """ResumeAgentUseCase module must not import langgraph directly."""
    import importlib
    import sys

    mod_name = "agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case"
    mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
    source = inspect.getsource(mod)
    assert (
        "from langgraph" not in source
    ), "resume_agent_use_case.py must not contain 'from langgraph' imports"
    assert (
        "import langgraph" not in source
    ), "resume_agent_use_case.py must not contain 'import langgraph' imports"
