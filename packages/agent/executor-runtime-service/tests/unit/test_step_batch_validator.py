"""Tests for StepBatchRequestValidator."""

import pytest

from app.layer2_application.features.validate_step_batch.use_cases.validate_step_batch_use_case import (
    ValidationError,
    validate_step_batch,
)

TOOL_SEARCH_ID = "tool.11111111-1111-4111-8111-111111111111"
AGENT_FILE_ID = "agent.22222222-2222-4222-8222-222222222222"
GENERIC_CAPABILITY_ID = "tool.33333333-3333-4333-8333-333333333333"


# ---------------------------------------------------------------------------
# Valid envelopes
# ---------------------------------------------------------------------------


def test_valid_single_step_batch():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.ingestion_business",
            "to": "executor.runtime",
            "type": "step_batch",
            "batch_id": "batch_001",
            "steps": [
                {
                    "id": "step_1",
                    "execute_by": TOOL_SEARCH_ID,
                    "input_data": {"input_text": "hello"},
                    "output_data": {},
                    "goal": "Test echo",
                    "message": "hello",
                }
            ],
        },
    }
    request = validate_step_batch(raw)
    assert request.message_id == "msg_001"
    assert request.correlation_id == "corr_001"
    assert request.conv_id == "conv_1"
    assert request.batch_id == "batch_001"
    assert request.from_agent == "agent.ingestion_business"
    assert len(request.steps) == 1
    assert request.steps[0].id == "step_1"
    assert request.steps[0].execute_by == TOOL_SEARCH_ID
    assert request.steps[0].input_data == {"input_text": "hello"}
    assert request.steps[0].output_data == {}


def test_valid_multi_step_batch():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_002",
        "correlation_id": "corr_002",
        "conv_id": "conv_2",
        "payload": {
            "from": "agent.analytics_business",
            "to": "executor.runtime",
            "type": "step_batch",
            "batch_id": "batch_002",
            "steps": [
                {
                    "id": "step_a",
                    "execute_by": TOOL_SEARCH_ID,
                    "input_data": {"query": "Search"},
                    "output_data": {},
                    "goal": "Search",
                },
                {
                    "id": "step_b",
                    "execute_by": AGENT_FILE_ID,
                    "input_data": {"file_id": "file_1"},
                    "output_data": {},
                    "goal": "Process file",
                },
            ],
        },
    }
    request = validate_step_batch(raw)
    assert len(request.steps) == 2
    assert request.steps[0].id == "step_a"
    assert request.steps[1].id == "step_b"


# ---------------------------------------------------------------------------
# Invalid envelopes
# ---------------------------------------------------------------------------


def test_wrong_type():
    raw = {
        "type": "agent.plan.created",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {"steps": [{"id": "s1", "execute_by": GENERIC_CAPABILITY_ID}]},
    }
    with pytest.raises(ValidationError, match="Expected type"):
        validate_step_batch(raw)


def test_missing_message_id():
    raw = {
        "type": "agent.request.executor",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {"steps": [{"id": "s1", "execute_by": GENERIC_CAPABILITY_ID}]},
    }
    with pytest.raises(ValidationError, match="message_id is required"):
        validate_step_batch(raw)


def test_missing_correlation_id():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "conv_id": "conv_1",
        "payload": {"steps": [{"id": "s1", "execute_by": GENERIC_CAPABILITY_ID}]},
    }
    with pytest.raises(ValidationError, match="correlation_id is required"):
        validate_step_batch(raw)


def test_missing_conv_id():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "payload": {"steps": [{"id": "s1", "execute_by": GENERIC_CAPABILITY_ID}]},
    }
    with pytest.raises(ValidationError, match="conv_id is required"):
        validate_step_batch(raw)


def test_empty_steps():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [],
        },
    }
    with pytest.raises(ValidationError, match="at least one step"):
        validate_step_batch(raw)


def test_missing_payload():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
    }
    with pytest.raises(ValidationError, match="payload must be an object"):
        validate_step_batch(raw)


def test_step_missing_id():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"execute_by": GENERIC_CAPABILITY_ID}],
        },
    }
    with pytest.raises(ValidationError, match="id is required"):
        validate_step_batch(raw)


def test_step_missing_execute_by():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"id": "step_1"}],
        },
    }
    with pytest.raises(ValidationError, match="missing execute_by"):
        validate_step_batch(raw)


def test_step_invalid_execute_by_prefix():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [
                {"id": "step_1", "execute_by": "11111111-1111-4111-8111-111111111111"}
            ],
        },
    }
    with pytest.raises(ValidationError, match=r"tool\.<tool_id>|agent\.<agent_id>"):
        validate_step_batch(raw)


def test_step_invalid_input_data_type():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [
                {
                    "id": "step_1",
                    "execute_by": GENERIC_CAPABILITY_ID,
                    "input_data": "not-an-object",
                }
            ],
        },
    }
    with pytest.raises(ValidationError, match="input_data must be an object"):
        validate_step_batch(raw)


def test_step_invalid_output_data_type():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [
                {
                    "id": "step_1",
                    "execute_by": GENERIC_CAPABILITY_ID,
                    "output_data": "not-an-object",
                }
            ],
        },
    }
    with pytest.raises(ValidationError, match="output_data must be an object"):
        validate_step_batch(raw)


def test_missing_payload_from():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"id": "step_1", "execute_by": GENERIC_CAPABILITY_ID}],
        },
    }
    with pytest.raises(ValidationError, match="payload.from is required"):
        validate_step_batch(raw)


def test_invalid_payload_to():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "agent.other",
            "type": "step_batch",
            "steps": [{"id": "step_1", "execute_by": GENERIC_CAPABILITY_ID}],
        },
    }
    with pytest.raises(ValidationError, match="payload.to must be 'executor.runtime'"):
        validate_step_batch(raw)


def test_invalid_payload_type():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_001",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step",
            "steps": [{"id": "step_1", "execute_by": GENERIC_CAPABILITY_ID}],
        },
    }
    with pytest.raises(ValidationError, match="payload.type must be 'step_batch'"):
        validate_step_batch(raw)


def test_batch_id_defaults_to_message_id():
    raw = {
        "type": "agent.request.executor",
        "message_id": "msg_fallback",
        "correlation_id": "corr_001",
        "conv_id": "conv_1",
        "payload": {
            "from": "agent.test",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"id": "s1", "execute_by": GENERIC_CAPABILITY_ID}],
        },
    }
    request = validate_step_batch(raw)
    assert request.batch_id == "msg_fallback"
