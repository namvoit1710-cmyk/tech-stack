"""Gap-filling unit specs for validate_step_batch.

Targets branch uncovered by test_step_batch_validator.py:
  - a non-dict element inside payload.steps (validate_step_batch_use_case.py:87)
plus boundary / edge cases grounded in the source.
"""

from __future__ import annotations

import pytest

from app.layer2_application.features.validate_step_batch.use_cases.validate_step_batch_use_case import (
    ValidationError,
    validate_step_batch,
    _is_supported_execute_by,
)

TOOL_ID = "tool.11111111-1111-4111-8111-111111111111"
AGENT_ID = "agent.22222222-2222-4222-8222-222222222222"


def _envelope(steps):
    return {
        "type": "agent.request.executor",
        "message_id": "msg_1",
        "correlation_id": "corr_1",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": steps,
        },
    }


# --- invalid input: a step element that is not an object (line 87) ---
def test_step_element_not_a_dict():
    raw = _envelope(["i-am-a-string"])
    with pytest.raises(ValidationError, match=r"payload.steps\[0\] must be an object"):
        validate_step_batch(raw)


# --- edge: agent-prefixed execute_by is accepted (alternate happy branch) ---
def test_agent_prefix_execute_by_accepted():
    raw = _envelope([{"id": "s1", "execute_by": AGENT_ID}])
    request = validate_step_batch(raw)
    assert request.steps[0].execute_by == AGENT_ID


# --- boundary: optional fields default correctly when omitted ---
def test_optional_fields_default_when_omitted():
    raw = _envelope([{"id": "s1", "execute_by": TOOL_ID}])
    request = validate_step_batch(raw)
    step = request.steps[0]
    assert step.input_data == {}
    assert step.output_data == {}
    assert step.goal == ""
    assert step.message == ""
    assert step.next_step_id is None
    assert step.uploaded_file_ids == []


# --- boundary: explicit None input_data/output_data coerced to {} (lines 108-109,117-118) ---
def test_none_input_output_data_coerced_to_empty_dict():
    raw = _envelope(
        [
            {
                "id": "s1",
                "execute_by": TOOL_ID,
                "input_data": None,
                "output_data": None,
                "uploaded_file_ids": None,
            }
        ]
    )
    request = validate_step_batch(raw)
    step = request.steps[0]
    assert step.input_data == {}
    assert step.output_data == {}
    assert step.uploaded_file_ids == []


# --- edge: uploaded_file_ids preserved when provided ---
def test_uploaded_file_ids_preserved():
    raw = _envelope(
        [{"id": "s1", "execute_by": TOOL_ID, "uploaded_file_ids": ["f1", "f2"]}]
    )
    request = validate_step_batch(raw)
    assert request.steps[0].uploaded_file_ids == ["f1", "f2"]


# --- failure path: type key present but None (falls back to "" -> mismatch) ---
def test_type_none_treated_as_empty():
    raw = _envelope([{"id": "s1", "execute_by": TOOL_ID}])
    raw["type"] = None
    with pytest.raises(ValidationError, match="Expected type"):
        validate_step_batch(raw)


# --- invalid input: payload is a list, not an object ---
def test_payload_is_list_rejected():
    raw = {
        "type": "agent.request.executor",
        "message_id": "m",
        "correlation_id": "c",
        "conv_id": "v",
        "payload": ["not", "a", "dict"],
    }
    with pytest.raises(ValidationError, match="payload must be an object"):
        validate_step_batch(raw)


# --- pure domain rule: _is_supported_execute_by regex boundaries ---
@pytest.mark.parametrize(
    "value,expected",
    [
        ("tool.11111111-1111-4111-8111-111111111111", True),
        ("agent.22222222-2222-4222-8222-222222222222", True),
        ("tool.not-a-uuid", False),
        ("service.11111111-1111-4111-8111-111111111111", False),  # wrong prefix
        ("tool.11111111-1111-4111-8111-111111111111 ", False),  # trailing space
        ("", False),
    ],
)
def test_is_supported_execute_by(value, expected):
    assert _is_supported_execute_by(value) is expected
