"""Tests for stateless HITL contracts.

Covers:
- HitlInterruptPayload entity
- HitlResumeCommand entity
- ExecuteAgentOutput carrying interrupt information
- ResumeAgentInput / ResumeAgentOutput contracts
"""

from dataclasses import fields

# ─── Domain entity: HitlInterruptPayload ───────────────────────────────────


def test_hitl_interrupt_payload_is_importable():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )

    assert HitlInterruptPayload is not None


def test_hitl_interrupt_payload_has_required_fields():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )

    field_names = {f.name for f in fields(HitlInterruptPayload)}
    # Required fields from spec
    assert "thread_id" in field_names, "HitlInterruptPayload must have thread_id"
    assert "interrupt_id" in field_names, "HitlInterruptPayload must have interrupt_id"
    assert "value" in field_names, "HitlInterruptPayload must have value"


def test_hitl_interrupt_payload_optional_tracking_fields():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )

    field_names = {f.name for f in fields(HitlInterruptPayload)}
    # Tracking metadata fields for future correlation
    assert "tenant_id" in field_names, "HitlInterruptPayload must have tenant_id"
    assert "user_id" in field_names, "HitlInterruptPayload must have user_id"
    assert "conv_id" in field_names, "HitlInterruptPayload must have conv_id"


def test_hitl_interrupt_payload_construction():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )

    payload = HitlInterruptPayload(
        thread_id="thread-abc",
        interrupt_id="int-123",
        value={"question": "Approve this action?"},
        tenant_id="tenant-1",
        user_id="user-1",
        conv_id="conv-1",
    )
    assert payload.thread_id == "thread-abc"
    assert payload.interrupt_id == "int-123"
    assert payload.value == {"question": "Approve this action?"}
    assert payload.tenant_id == "tenant-1"
    assert payload.user_id == "user-1"
    assert payload.conv_id == "conv-1"


def test_hitl_interrupt_payload_default_tracking_fields():
    """Tracking fields should have sensible defaults so they can be omitted."""
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )

    payload = HitlInterruptPayload(
        thread_id="thread-xyz",
        interrupt_id="int-456",
        value="Please confirm",
    )
    # Should not raise; defaults should work
    assert payload.thread_id == "thread-xyz"
    assert payload.interrupt_id == "int-456"


# ─── Domain entity: HitlResumeCommand ─────────────────────────────────────


def test_hitl_resume_command_is_importable():
    from agent_sdk.layer1_domain.entities.hitl_resume_command import HitlResumeCommand

    assert HitlResumeCommand is not None


def test_hitl_resume_command_has_required_fields():
    from agent_sdk.layer1_domain.entities.hitl_resume_command import HitlResumeCommand

    field_names = {f.name for f in fields(HitlResumeCommand)}
    assert "thread_id" in field_names, "HitlResumeCommand must have thread_id"
    assert "resume_value" in field_names, "HitlResumeCommand must have resume_value"


def test_hitl_resume_command_optional_interrupt_id():
    """interrupt_id should be optional (resume by thread alone when omitted)."""
    from agent_sdk.layer1_domain.entities.hitl_resume_command import HitlResumeCommand

    field_names = {f.name for f in fields(HitlResumeCommand)}
    assert (
        "interrupt_id" in field_names
    ), "HitlResumeCommand must have optional interrupt_id"


def test_hitl_resume_command_construction():
    from agent_sdk.layer1_domain.entities.hitl_resume_command import HitlResumeCommand

    cmd = HitlResumeCommand(
        thread_id="thread-abc",
        resume_value="approved",
        interrupt_id="int-123",
    )
    assert cmd.thread_id == "thread-abc"
    assert cmd.resume_value == "approved"
    assert cmd.interrupt_id == "int-123"


def test_hitl_resume_command_without_interrupt_id():
    from agent_sdk.layer1_domain.entities.hitl_resume_command import HitlResumeCommand

    cmd = HitlResumeCommand(thread_id="thread-xyz", resume_value={"approved": True})
    assert cmd.interrupt_id is None


# ─── ExecuteAgentOutput: interrupt fields ─────────────────────────────────


def test_execute_agent_output_has_interrupted_field():
    """ExecuteAgentOutput must carry an `interrupted` bool flag."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )

    output = ExecuteAgentOutput()
    assert hasattr(
        output, "interrupted"
    ), "ExecuteAgentOutput must have `interrupted` field"
    assert output.interrupted is False, "interrupted should default to False"


def test_execute_agent_output_has_interrupt_payload_field():
    """ExecuteAgentOutput must carry an optional `interrupt_payload` field."""
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )

    output = ExecuteAgentOutput()
    assert hasattr(
        output, "interrupt_payload"
    ), "ExecuteAgentOutput must have `interrupt_payload` field"
    assert output.interrupt_payload is None, "interrupt_payload should default to None"


def test_execute_agent_output_interrupted_with_payload():
    """ExecuteAgentOutput can carry an interrupt payload when interrupted=True."""
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import (
        HitlInterruptPayload,
    )
    from agent_sdk.layer2_application.features.execute_agent.use_cases.execute_agent_use_case import (
        ExecuteAgentOutput,
    )

    payload = HitlInterruptPayload(thread_id="t1", interrupt_id="i1", value="confirm?")
    output = ExecuteAgentOutput(
        status="interrupted",
        interrupted=True,
        interrupt_payload=payload,
    )
    assert output.interrupted is True
    assert output.status == "interrupted"
    assert output.interrupt_payload is payload


# ─── ResumeAgentInput / ResumeAgentOutput ─────────────────────────────────


def test_resume_agent_input_is_importable():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
    )

    assert ResumeAgentInput is not None


def test_resume_agent_output_is_importable():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentOutput,
    )

    assert ResumeAgentOutput is not None


def test_resume_agent_input_fields():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
    )

    field_names = {f.name for f in fields(ResumeAgentInput)}
    assert "thread_id" in field_names
    assert "resume_value" in field_names


def test_resume_agent_input_optional_interrupt_id():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
    )

    field_names = {f.name for f in fields(ResumeAgentInput)}
    assert "interrupt_id" in field_names


def test_resume_agent_input_construction_defaults():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentInput,
    )

    inp = ResumeAgentInput(thread_id="t1", resume_value="yes")
    assert inp.thread_id == "t1"
    assert inp.resume_value == "yes"
    assert inp.interrupt_id is None


def test_resume_agent_output_fields():
    from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
        ResumeAgentOutput,
    )

    out = ResumeAgentOutput()
    # Should have same shape as ExecuteAgentOutput
    assert hasattr(out, "message")
    assert hasattr(out, "status")
    assert hasattr(out, "error")


# ─── Public API exports ────────────────────────────────────────────────────


def test_hitl_types_exported_from_agent_sdk():
    """HitlInterruptPayload, HitlResumeCommand should be importable from agent_sdk."""
    from agent_sdk import HitlInterruptPayload, HitlResumeCommand

    assert HitlInterruptPayload is not None
    assert HitlResumeCommand is not None


def test_resume_use_case_exported_from_agent_sdk():
    """ResumeAgentUseCase should be importable from agent_sdk."""
    from agent_sdk import ResumeAgentUseCase

    assert ResumeAgentUseCase is not None


# ─── InterruptType enum values ────────────────────────────────────────────


def test_interrupt_type_enum_values():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import InterruptType

    assert InterruptType.PERMISSION_REQUEST == "PERMISSION_REQUEST"


def test_interrupt_type_has_agent_call():
    from agent_sdk.layer1_domain.entities.hitl_interrupt_payload import InterruptType

    assert InterruptType.AGENT_CALL == "AGENT_CALL"
