"""
Tests for update Request/Response Entities & DTOs.
"""

import uuid
from dataclasses import fields

from tests.helpers.testing import StubLogger, StubMonitor


def test_request_context_dataclass_exists():
    from agent_sdk.layer1_domain.entities.agent_request import RequestContext

    ctx = RequestContext(agent="my-agent", source="api", history=[])
    assert ctx.agent == "my-agent"
    assert ctx.source == "api"
    assert ctx.history == []


def test_request_context_defaults():
    from agent_sdk.layer1_domain.entities.agent_request import RequestContext

    ctx = RequestContext(agent="my-agent")
    assert ctx.source == "api"
    assert ctx.history == []


def test_agent_request_has_context_field():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    field_names = {f.name for f in fields(AgentRequest)}
    assert "context" in field_names


def test_agent_request_context_defaults_none():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    req = AgentRequest(message="hello")
    assert req.context is None


def test_agent_request_context_can_be_set():
    from agent_sdk.layer1_domain.entities.agent_request import (
        AgentRequest,
        RequestContext,
    )

    ctx = RequestContext(agent="bot", source="kafka", history=["msg1"])
    req = AgentRequest(message="hello", context=ctx)
    assert req.context.agent == "bot"
    assert req.context.source == "kafka"


def test_agent_response_has_conv_id():
    from agent_sdk.layer1_domain.entities.agent_response import AgentResponse

    field_names = {f.name for f in fields(AgentResponse)}
    assert "conv_id" in field_names


def test_agent_response_has_session_id():
    from agent_sdk.layer1_domain.entities.agent_response import AgentResponse

    field_names = {f.name for f in fields(AgentResponse)}
    assert "session_id" in field_names


def test_agent_response_has_agent():
    from agent_sdk.layer1_domain.entities.agent_response import AgentResponse

    field_names = {f.name for f in fields(AgentResponse)}
    assert "agent" in field_names


def test_agent_response_has_agent_data_not_data():
    from agent_sdk.layer1_domain.entities.agent_response import AgentResponse

    field_names = {f.name for f in fields(AgentResponse)}
    assert "agent_data" in field_names
    assert "data" not in field_names


def test_agent_response_defaults():
    from agent_sdk.layer1_domain.entities.agent_response import AgentResponse

    resp = AgentResponse(message="done")
    assert resp.conv_id == ""
    assert resp.session_id == ""
    assert resp.agent == ""
    assert resp.agent_data == {}
    assert resp.status == "success"
    assert resp.error is None


def test_agent_base_state_has_context():
    from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

    assert "context" in AgentBaseState.__annotations__


def test_agent_state_imports_request_context():
    from agent_sdk.layer1_domain.entities.agent_request import RequestContext
    from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

    state: AgentBaseState = {"context": RequestContext(agent="bot")}
    assert state["context"].agent == "bot"


def test_execute_agent_input_has_session_id():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    field_names = {f.name for f in fields(ExecuteAgentInput)}
    assert "session_id" in field_names


def test_execute_agent_input_session_id_defaults_empty():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    inp = ExecuteAgentInput(message="hello")
    assert inp.session_id == ""


def test_execute_agent_output_has_session_id():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )

    field_names = {f.name for f in fields(ExecuteAgentOutput)}
    assert "session_id" in field_names


def test_execute_agent_output_has_agent_data_not_data():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )

    field_names = {f.name for f in fields(ExecuteAgentOutput)}
    assert "agent_data" in field_names
    assert "data" not in field_names


def test_execute_agent_output_session_id_defaults_empty():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )

    out = ExecuteAgentOutput()
    assert out.session_id == ""


import pytest


@pytest.mark.asyncio
async def test_execute_generates_session_id_if_missing():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    uc = ExecuteAgentUseCase(logger=StubLogger(), monitor=StubMonitor())
    req = ExecuteAgentInput(message="hello", session_id="")
    result = await uc.execute(req)
    assert result.session_id != ""
    try:
        uuid.UUID(result.session_id)
    except ValueError:
        raise AssertionError(f"session_id '{result.session_id}' is not a valid UUID")


@pytest.mark.asyncio
async def test_execute_preserves_provided_session_id():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
        ExecuteAgentUseCase,
    )

    provided_id = str(uuid.uuid4())
    uc = ExecuteAgentUseCase(logger=StubLogger(), monitor=StubMonitor())
    req = ExecuteAgentInput(message="hello", session_id=provided_id)
    result = await uc.execute(req)
    assert result.session_id == provided_id
