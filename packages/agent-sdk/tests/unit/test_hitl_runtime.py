"""Tests for stateless HITL runtime behavior.

Covers:
- ExecuteAgentUseCase catching GraphInterrupt and returning interrupted output
- ResumeAgentUseCase executing resume via Command(resume=...)
- Resume endpoint in routes.py
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.errors import GraphInterrupt
from langgraph.types import Command, Interrupt

from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime
from tests.helpers.testing import StubLogger as _StubLogger
from tests.helpers.testing import StubMonitor as _StubMonitor

# ─── ExecuteAgentUseCase: GraphInterrupt handling ─────────────────────────


@pytest.mark.asyncio
async def test_execute_use_case_catches_graph_interrupt():
    """When graph raises GraphInterrupt, execute() should return interrupted output."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentOutput,
        ExecuteAgentUseCase,
    )

    interrupt_obj = Interrupt(value="Please approve the action", id="int-abc")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Hello",
        conv_id="conv-interrupt-1",
        user_id="user-1",
        tenant_id="tenant-1",
    )
    result = await use_case.execute(request)

    assert isinstance(result, ExecuteAgentOutput)
    assert result.interrupted is True
    assert result.status == "interrupted"
    assert result.error is None  # Not an error — expected HITL pause


@pytest.mark.asyncio
async def test_execute_use_case_interrupt_payload_contains_thread_id():
    """interrupt_payload.thread_id must equal the conv_id used as thread_id."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    interrupt_obj = Interrupt(value={"question": "Confirm?"}, id="int-xyz")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Run task",
        conv_id="conv-thread-99",
        user_id="u1",
        tenant_id="t1",
    )
    result = await use_case.execute(request)

    assert result.interrupted is True
    assert result.interrupt_payload is not None
    assert result.interrupt_payload.thread_id == "conv-thread-99"


@pytest.mark.asyncio
async def test_execute_use_case_interrupt_payload_contains_interrupt_id():
    """interrupt_payload.interrupt_id must match the Interrupt.id from LangGraph."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    interrupt_obj = Interrupt(value="confirm", id="interrupt-id-007")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Run",
        conv_id="conv-1",
        user_id="u1",
        tenant_id="t1",
    )
    result = await use_case.execute(request)

    assert result.interrupt_payload.interrupt_id == "interrupt-id-007"


@pytest.mark.asyncio
async def test_execute_use_case_interrupt_payload_contains_value():
    """interrupt_payload.value must carry the interrupt's value."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    interrupt_obj = Interrupt(value={"question": "Approve?"}, id="int-42")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Go", conv_id="c1", user_id="u1", tenant_id="t1"
    )
    result = await use_case.execute(request)

    assert result.interrupt_payload.value == {"question": "Approve?"}


@pytest.mark.asyncio
async def test_execute_use_case_interrupt_payload_carries_tracking_metadata():
    """interrupt_payload should carry tenant/user/conversation ids for correlation."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    interrupt_obj = Interrupt(value="approve?", id="int-meta")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Go",
        conv_id="conv-meta-1",
        user_id="user-meta",
        tenant_id="tenant-meta",
    )
    result = await use_case.execute(request)

    payload = result.interrupt_payload
    assert payload.tenant_id == "tenant-meta"
    assert payload.user_id == "user-meta"
    assert payload.conv_id == "conv-meta-1"


@pytest.mark.asyncio
async def test_execute_use_case_interrupt_tracks_metric():
    """agent_execute_interrupted metric should be tracked on GraphInterrupt."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    interrupt_obj = Interrupt(value="ok?", id="int-metric")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    monitor = _StubMonitor()
    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=monitor, agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Run", conv_id="c1", user_id="u1", tenant_id="t1"
    )
    await use_case.execute(request)

    assert monitor.tracked.get("agent_execute_interrupted", 0) == 1


@pytest.mark.asyncio
async def test_execute_use_case_normal_exception_still_returns_error():
    """Non-GraphInterrupt exceptions must still yield error output (no regression)."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentOutput,
        ExecuteAgentUseCase,
    )

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("boom"))

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_graph=mock_graph
    )
    request = ExecuteAgentInput(
        message="Go", conv_id="c1", user_id="u1", tenant_id="t1"
    )
    result = await use_case.execute(request)

    assert isinstance(result, ExecuteAgentOutput)
    assert result.interrupted is False
    assert result.status == "error"
    assert result.error is not None


@pytest.mark.asyncio
async def test_execute_use_case_preserves_semantic_node_error_context():
    from agent_sdk.layer1_domain.exceptions import NodeExecutionError
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        side_effect=NodeExecutionError(
            "fallback",
            error_context={
                "exception_type": "WorkflowKnownIssueException",
                "message": "Downstream planner unavailable",
                "error_code": "WORKFLOW_KNOWN_ISSUE",
                "related_step_id": "planner-step",
                "is_critical": True,
                "metadata": {"system": "planner"},
            },
        )
    )

    publisher = AsyncMock()
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )

    use_case = ExecuteAgentUseCase(
        logger=_StubLogger(),
        monitor=_StubMonitor(),
        agent_graph=mock_graph,
        workflow_event_emitter=WorkflowEventEmitter(publisher=publisher),
    )

    result = await use_case.execute(
        ExecuteAgentInput(message="Go", conv_id="main_semantic_1")
    )

    assert result.status == "error"
    assert result.error == "Downstream planner unavailable"
    assert result.error_code == "WORKFLOW_KNOWN_ISSUE"
    assert result.related_step_id == "planner-step"
    assert result.is_critical is True
    assert result.error_context == {
        "exception_type": "WorkflowKnownIssueException",
        "message": "Downstream planner unavailable",
        "error_code": "WORKFLOW_KNOWN_ISSUE",
        "related_step_id": "planner-step",
        "is_critical": True,
        "metadata": {"system": "planner"},
    }

    event_payload = publisher.publish.call_args.args[1]
    assert event_payload["type"] == "agent.plan.error"
    assert event_payload["payload"]["error_code"] == "WORKFLOW_KNOWN_ISSUE"


@pytest.mark.asyncio
async def test_resume_use_case_preserves_semantic_node_error_context():
    from agent_sdk.layer1_domain.exceptions import NodeExecutionError
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )
    from agent_sdk.layer2_application.services.workflow_event_emitter import (
        WorkflowEventEmitter,
    )
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        side_effect=NodeExecutionError(
            "fallback",
            error_context={
                "exception_type": "DelegationTimeoutException",
                "message": "Delegate timed out",
                "error_code": "DELEGATION_TIMEOUT",
                "related_step_id": "delegate-step",
                "is_critical": False,
                "metadata": {"agent_type": "worker"},
            },
        )
    )
    publisher = AsyncMock()

    use_case = ResumeAgentUseCase(
        logger=_StubLogger(),
        monitor=_StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
        workflow_event_emitter=WorkflowEventEmitter(publisher=publisher),
    )

    result = await use_case.execute(
        ResumeAgentInput(thread_id="main_semantic_2", resume_value="ok")
    )

    assert result.status == "error"
    assert result.error == "Delegate timed out"
    assert result.error_code == "DELEGATION_TIMEOUT"
    assert result.related_step_id == "delegate-step"
    assert result.is_critical is False
    assert result.error_context == {
        "exception_type": "DelegationTimeoutException",
        "message": "Delegate timed out",
        "error_code": "DELEGATION_TIMEOUT",
        "related_step_id": "delegate-step",
        "is_critical": False,
        "metadata": {"agent_type": "worker"},
    }


@pytest.mark.asyncio
async def test_resume_use_case_preserves_normalized_final_state_error_metadata():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(
        return_value={
            "status": "error",
            "message": "Delegate timed out",
            "error": "Delegate timed out",
            "error_code": "DELEGATION_TIMEOUT",
            "related_step_id": "delegate-step",
            "is_critical": True,
            "error_context": {
                "exception_type": "DelegationTimeoutException",
                "message": "Delegate timed out",
                "error_code": "DELEGATION_TIMEOUT",
                "related_step_id": "delegate-step",
                "is_critical": True,
                "metadata": {"agent_type": "worker"},
            },
        }
    )

    use_case = ResumeAgentUseCase(
        logger=_StubLogger(),
        monitor=_StubMonitor(),
        agent_runtime=LangGraphRuntime(mock_graph),
    )

    result = await use_case.execute(
        ResumeAgentInput(thread_id="main_semantic_3", resume_value="ok")
    )

    assert result.status == "error"
    assert result.related_step_id == "delegate-step"
    assert result.is_critical is True
    assert result.error_context == {
        "exception_type": "DelegationTimeoutException",
        "message": "Delegate timed out",
        "error_code": "DELEGATION_TIMEOUT",
        "related_step_id": "delegate-step",
        "is_critical": True,
        "metadata": {"agent_type": "worker"},
    }


# ─── ResumeAgentUseCase ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resume_use_case_is_importable():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentUseCase,
    )

    assert ResumeAgentUseCase is not None


@pytest.mark.asyncio
async def test_resume_use_case_invokes_graph_with_command():
    """LangGraphRuntime.resume must call graph.ainvoke with Command(resume=resume_value)."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    formatted_state = {
        "formatted_response": {"content": "Resumed OK", "status": "success"}
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    inp = ResumeAgentInput(thread_id="conv-resume-1", resume_value="approved")
    await use_case.execute(inp)

    assert mock_graph.ainvoke.called
    call_args = mock_graph.ainvoke.call_args
    command_arg = call_args[0][0]
    # LangGraphRuntime constructs the Command; use case passes raw resume_value
    assert isinstance(command_arg, Command)
    assert command_arg.resume == "approved"


@pytest.mark.asyncio
async def test_resume_use_case_passes_thread_id_in_config():
    """ResumeAgentUseCase must use thread_id in config so checkpointer restores state."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    formatted_state = {"formatted_response": {"content": "OK", "status": "success"}}
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    inp = ResumeAgentInput(thread_id="conv-thread-123", resume_value="yes")
    await use_case.execute(inp)

    call_args = mock_graph.ainvoke.call_args
    # call_args[0] is positional args. [0] is Command, [1] is config
    config = call_args[0][1]
    assert config.get("configurable", {}).get("thread_id") == "conv-thread-123"


@pytest.mark.asyncio
async def test_resume_use_case_returns_output_on_success():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentOutput,
        ResumeAgentUseCase,
    )

    formatted_state = {
        "formatted_response": {"content": "Post-resume answer", "status": "success"}
    }
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(return_value=formatted_state)

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t1", resume_value="approved")
    )

    assert isinstance(result, ResumeAgentOutput)
    assert result.message == "Post-resume answer"
    assert result.status == "success"
    assert result.error is None


@pytest.mark.asyncio
async def test_resume_use_case_returns_error_on_exception():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentOutput,
        ResumeAgentUseCase,
    )

    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("graph crashed"))

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    result = await use_case.execute(
        ResumeAgentInput(thread_id="t1", resume_value="approved")
    )

    assert isinstance(result, ResumeAgentOutput)
    assert result.status == "error"
    assert result.error is not None
    assert "graph crashed" in result.error


@pytest.mark.asyncio
async def test_resume_use_case_handles_resumed_interrupt():
    """If resume itself triggers another interrupt, return interrupted output."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentOutput,
        ResumeAgentUseCase,
    )

    interrupt_obj = Interrupt(value="step 2 confirm?", id="int-step2")
    mock_graph = MagicMock()
    mock_graph.ainvoke = AsyncMock(side_effect=GraphInterrupt([interrupt_obj]))

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    result = await use_case.execute(ResumeAgentInput(thread_id="t1", resume_value="go"))

    assert isinstance(result, ResumeAgentOutput)
    assert result.interrupted is True
    assert result.status == "interrupted"
    assert result.interrupt_payload is not None
    assert result.interrupt_payload.interrupt_id == "int-step2"


# ─── Resume endpoint in routes.py ─────────────────────────────────────────


def test_resume_endpoint_registered_in_router():
    """create_router should register a POST /resume endpoint when resume_agent is present."""
    from fastapi.routing import APIRoute

    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        create_router,
    )

    class _StubResumeUseCase:
        async def execute(self, inp):
            from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
                ResumeAgentOutput,
            )

            return ResumeAgentOutput(message="resumed", status="success")

    container = {"resume_agent": _StubResumeUseCase()}
    router = create_router(container)

    resume_route = None
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == "/resume":
            resume_route = route
            break

    assert resume_route is not None, "POST /resume route must be registered"
    assert "POST" in resume_route.methods, "/resume must accept POST"


@pytest.mark.asyncio
async def test_resume_endpoint_calls_use_case():
    """POST /resume should delegate to resume_agent use case and return output."""
    from httpx import ASGITransport, AsyncClient

    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentOutput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubResumeUseCase:
        def __init__(self):
            self.received_input = None

        async def execute(self, inp: ResumeAgentInput) -> ResumeAgentOutput:
            self.received_input = inp
            return ResumeAgentOutput(
                message="resumed successfully",
                status="success",
            )

    stub = _StubResumeUseCase()
    container = {"resume_agent": stub}
    app = create_agent_app(container)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/resume",
            json={"thread_id": "conv-1", "resume_value": "approved"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "resumed successfully"
    assert data["status"] == "success"
    assert stub.received_input is not None
    assert stub.received_input.thread_id == "conv-1"
    assert stub.received_input.resume_value == "approved"


@pytest.mark.asyncio
async def test_resume_endpoint_returns_interrupted_output():
    """POST /resume should propagate interrupted=True and interrupt_payload from use case."""
    from httpx import ASGITransport, AsyncClient

    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentOutput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubResumeUseCase:
        async def execute(self, inp: ResumeAgentInput) -> ResumeAgentOutput:
            payload = HitlInterruptPayload(
                thread_id="conv-1",
                interrupt_id="int-step2",
                value="confirm step 2?",
            )
            return ResumeAgentOutput(
                status="interrupted",
                interrupted=True,
                interrupt_payload=payload,
            )

    container = {"resume_agent": _StubResumeUseCase()}
    app = create_agent_app(container)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/resume",
            json={"thread_id": "conv-1", "resume_value": "yes"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "interrupted"
    assert data["interrupted"] is True
    assert data["interrupt_payload"] is not None
    assert data["interrupt_payload"]["interrupt_id"] == "int-step2"


@pytest.mark.asyncio
async def test_resume_endpoint_interrupt_payload_includes_type_and_message():
    """POST /resume must preserve interrupt payload type/message fields in HTTP output."""
    from httpx import ASGITransport, AsyncClient

    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
        InterruptType,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentOutput,
    )
    from agent_sdk.layer3_adapters.presenters.agent_server import create_agent_app

    class _StubResumeUseCase:
        async def execute(self, inp: ResumeAgentInput) -> ResumeAgentOutput:
            payload = HitlInterruptPayload(
                thread_id="conv-1",
                interrupt_id="int-step2",
                value={"question": "confirm step 2?"},
                type=InterruptType.CONFIRMATION,
                message="Please confirm step 2",
            )
            return ResumeAgentOutput(
                status="interrupted",
                interrupted=True,
                interrupt_payload=payload,
            )

    container = {"resume_agent": _StubResumeUseCase()}
    app = create_agent_app(container)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.post(
            "/api/v1/resume",
            json={"thread_id": "conv-1", "resume_value": "yes"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["interrupt_payload"]["type"] == "CONFIRMATION"
    assert data["interrupt_payload"]["message"] == "Please confirm step 2"


# ─── Empty / missing interrupt_id must use scalar Command(resume=...) ─────


@pytest.mark.asyncio
async def test_resume_with_empty_string_interrupt_id_uses_scalar_command():
    """LangGraphRuntime.resume with interrupt_id='' must use scalar Command(resume=value)
    not the targeted map syntax Command(resume={interrupt_id: value}).

    An empty string interrupt_id is effectively absent; routing to a map key of ""
    will fail in LangGraph because no node listens on that key.
    """
    from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

    captured_commands = []
    mock_graph = MagicMock()

    async def ainvoke(cmd, config):
        captured_commands.append(cmd)
        return {"formatted_response": {"content": "ok", "status": "success"}}

    mock_graph.ainvoke = ainvoke

    runtime = LangGraphRuntime(mock_graph)
    await runtime.resume(
        resume_value="approved",
        config={"configurable": {"thread_id": "t1"}},
        interrupt_id="",
    )

    assert len(captured_commands) == 1
    cmd = captured_commands[0]
    assert isinstance(cmd, Command)
    assert not isinstance(cmd.resume, dict), (
        "interrupt_id='' must produce scalar Command(resume='approved'), "
        f"but got Command(resume={cmd.resume!r})"
    )
    assert cmd.resume == "approved"


@pytest.mark.asyncio
async def test_resume_use_case_with_empty_interrupt_id_uses_scalar_command():
    """ResumeAgentUseCase.execute with interrupt_id='' must pass scalar resume Command
    to the graph, not a dict keyed on empty string."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
        ResumeAgentUseCase,
    )

    captured_commands = []
    mock_graph = MagicMock()

    async def ainvoke(cmd, config):
        captured_commands.append(cmd)
        return {"formatted_response": {"content": "ok", "status": "success"}}

    mock_graph.ainvoke = ainvoke

    runtime = LangGraphRuntime(mock_graph)
    use_case = ResumeAgentUseCase(
        logger=_StubLogger(), monitor=_StubMonitor(), agent_runtime=runtime
    )
    await use_case.execute(
        ResumeAgentInput(
            thread_id="t-empty-iid",
            resume_value={"result": "done"},
            interrupt_id="",
        )
    )

    assert len(captured_commands) == 1
    cmd = captured_commands[0]
    assert isinstance(cmd, Command)
    assert cmd.resume == {"result": "done"}, (
        "interrupt_id='' must yield scalar Command(resume=resume_value) passing the "
        "resume_value directly, without wrapping it under an empty-string key. "
        f"got Command(resume={cmd.resume!r})"
    )
    assert "" not in (
        cmd.resume if isinstance(cmd.resume, dict) else {}
    ), "interrupt_id='' must not produce Command(resume={'': ...}) map syntax"
