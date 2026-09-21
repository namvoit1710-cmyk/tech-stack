"""
SA-1778 regression: business-agent built the agent-sdk Metadata with `conversation_id=`
(a field that doesn't exist) on the sub-conversation branch, so pydantic raised
"1 validation error for Metadata: conv_id Field required" and the request 500'd.

These tests encode the bug at the contract boundary (repro-first) and guard against the
`conversation_id` misuse reappearing in create_plan_usecase.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_sdk.layer1_domain.entities.agent_shared_state import Metadata, MetadataStatus

_USECASE = (
    Path(__file__).resolve().parents[2]
    / "app/layer2_application/features/main_agent/use_cases/create_plan_usecase.py"
)


def test_metadata_builds_with_conv_id_like_the_sub_conversation_branch():
    """The fixed builder shape (conv_id=…) validates and round-trips."""
    generated_id = "019f-generated"
    md = Metadata(
        conv_id=generated_id,
        main_conv_id="parent-conv",
        checkpoint_id=generated_id,
        parent_checkpoint="cp-parent",
        last_event_id=None,
        correlation_id=generated_id,
        branch="sub",
        current_agent="agent.x",
        status=MetadataStatus.IN_PROGRESS,
        current_task_id=None,
        current_run_id="run-1",
        current_node_id="node-1",
    )
    assert md.conv_id == generated_id
    assert md.main_conv_id == "parent-conv"
    # the field really is conv_id, not conversation_id
    assert not hasattr(md, "conversation_id")


def test_metadata_rejects_conversation_id_kwarg_reproduces_bug():
    """The pre-fix shape (conversation_id=…) fails with conv_id Field required."""
    with pytest.raises(ValidationError) as exc:
        Metadata(conversation_id="019f-generated", checkpoint_id="cp")  # type: ignore[call-arg]
    assert "conv_id" in str(exc.value)


def test_create_plan_usecase_has_no_conversation_id_misuse():
    """Recurrence guard: no Metadata(conversation_id=… ) and no metadata.conversation_id."""
    src = _USECASE.read_text(encoding="utf-8")
    assert "Metadata(conversation_id" not in src
    assert "metadata.conversation_id" not in src
