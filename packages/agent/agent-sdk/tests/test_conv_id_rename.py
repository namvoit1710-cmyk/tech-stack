"""Tests for conv_id rename from conversation_id across all SDK entities."""

from dataclasses import fields


def test_agent_request_has_conv_id():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    field_names = {f.name for f in fields(AgentRequest)}
    assert "conv_id" in field_names, "AgentRequest must have conv_id"
    assert (
        "conversation_id" not in field_names
    ), "AgentRequest must not have conversation_id"


def test_agent_request_conv_id_default():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest

    req = AgentRequest(message="hello")
    assert req.conv_id == ""


def test_agent_base_state_has_conv_id():
    from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

    assert "conv_id" in AgentBaseState.__annotations__
    assert "conversation_id" not in AgentBaseState.__annotations__


def test_tenant_context_has_conv_id():
    from agent_sdk.layer1_domain.entities.tenant_context import TenantContext

    field_names = {f.name for f in fields(TenantContext)}
    assert "conv_id" in field_names, "TenantContext must have conv_id"
    assert (
        "conversation_id" not in field_names
    ), "TenantContext must not have conversation_id"


def test_tenant_context_from_request_uses_conv_id():
    from agent_sdk.layer1_domain.entities.agent_request import AgentRequest
    from agent_sdk.layer1_domain.entities.tenant_context import TenantContext

    req = AgentRequest(message="hi", conv_id="conv-abc")
    ctx = TenantContext.from_request(req)
    assert ctx.conv_id == "conv-abc"


def test_hitl_interrupt_payload_has_conv_id():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )

    field_names = {f.name for f in fields(HitlInterruptPayload)}
    assert "conv_id" in field_names, "HitlInterruptPayload must have conv_id"
    assert (
        "conversation_id" not in field_names
    ), "HitlInterruptPayload must not have conversation_id"


def test_execute_agent_input_has_conv_id():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentInput,
    )

    field_names = {f.name for f in fields(ExecuteAgentInput)}
    assert "conv_id" in field_names, "ExecuteAgentInput must have conv_id"
    assert (
        "conversation_id" not in field_names
    ), "ExecuteAgentInput must not have conversation_id"


def test_execute_agent_pydantic_has_conv_id():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        ExecuteAgentInputPydantic,
    )

    assert "conv_id" in ExecuteAgentInputPydantic.model_fields
    assert "conversation_id" not in ExecuteAgentInputPydantic.model_fields


def test_hitl_interrupt_payload_pydantic_has_conv_id():
    from agent_sdk.layer3_adapters.presenters.agent_server.v1.routes import (
        HitlInterruptPayloadPydantic,
    )

    assert "conv_id" in HitlInterruptPayloadPydantic.model_fields
    assert "conversation_id" not in HitlInterruptPayloadPydantic.model_fields
