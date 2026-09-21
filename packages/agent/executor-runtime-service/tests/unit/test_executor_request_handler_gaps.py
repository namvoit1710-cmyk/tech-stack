"""Gap-filling unit specs for ExecutorQueueHandler.

Targets branches uncovered by test_executor_request_handler.py — chiefly the
best-effort validation-error publisher (_publish_validation_error) edge cases:
  - payload.payload is not an object (executor_queue_handler.py:164-167)
  - no steps / steps not a list (173-176)
  - a non-dict step element is skipped (180)
and the success-output propagation onto the step (110-111) via the terminal event.

Collaborators: dispatcher (DispatchStepUseCase, has async .dispatch) -> AsyncMock;
status_publisher (async .publish_status) -> AsyncMock; logger (sync) -> MagicMock.
Expectations grounded in executor_queue_handler.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.layer1_domain.entities.step import StepResult, StepStatus
from app.layer2_application.queues.executor_queue_handler import ExecutorQueueHandler

TOOL_ID = "tool.11111111-1111-4111-8111-111111111111"


def _handler(dispatch_result=None):
    dispatcher = AsyncMock()
    dispatcher.dispatch.return_value = dispatch_result or StepResult(
        step_id="s1", status=StepStatus.SUCCESS, output={"result": "done"}
    )
    publisher = AsyncMock()
    logger = MagicMock()
    handler = ExecutorQueueHandler(
        dispatcher=dispatcher,
        status_publisher=publisher,
        logger=logger,
    )
    return handler, dispatcher, publisher, logger


# --- happy path: success result output is copied onto the step (lines 108-111) ---
@pytest.mark.asyncio
async def test_success_output_propagated_to_terminal_status():
    handler, dispatcher, publisher, _logger = _handler(
        StepResult(step_id="s1", status=StepStatus.SUCCESS, output={"result": "hi"})
    )
    payload = {
        "type": "agent.request.executor",
        "message_id": "m1",
        "correlation_id": "c1",
        "conv_id": "v1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"id": "s1", "execute_by": TOOL_ID}],
        },
    }
    await handler.handle_step_batch(payload)

    # processing + terminal published
    assert publisher.publish_status.await_count == 2
    terminal_input = publisher.publish_status.await_args_list[1].args[0]
    assert terminal_input.step.status == StepStatus.SUCCESS
    assert terminal_input.step.output_data == {"result": "hi"}


# --- edge: an ERROR dispatch result keeps output unchanged, sets error ---
@pytest.mark.asyncio
async def test_error_result_sets_error_not_output():
    handler, _dispatcher, publisher, _logger = _handler(
        StepResult(step_id="s1", status=StepStatus.ERROR, error="nope")
    )
    payload = {
        "type": "agent.request.executor",
        "message_id": "m1",
        "correlation_id": "c1",
        "conv_id": "v1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"id": "s1", "execute_by": TOOL_ID, "output_data": {"pre": 1}}],
        },
    }
    await handler.handle_step_batch(payload)

    terminal_input = publisher.publish_status.await_args_list[1].args[0]
    assert terminal_input.step.status == StepStatus.ERROR
    assert terminal_input.step.error == "nope"
    # output_data NOT overwritten with success output on the error branch
    assert terminal_input.step.output_data == {"pre": 1}


# --- failure path: validation fails and inner payload is not an object (164-167) ---
@pytest.mark.asyncio
async def test_validation_error_payload_not_object_logs_and_no_publish():
    handler, dispatcher, publisher, logger = _handler()
    # Missing message_id -> ValidationError; payload is a string, not a dict.
    payload = {
        "type": "agent.request.executor",
        "correlation_id": "c1",
        "conv_id": "v1",
        "payload": "not-an-object",
    }
    await handler.handle_step_batch(payload)

    dispatcher.dispatch.assert_not_awaited()
    publisher.publish_status.assert_not_awaited()
    assert logger.error.called


# --- failure path: validation fails and steps are missing/not a list (173-176) ---
@pytest.mark.asyncio
async def test_validation_error_no_steps_logs_and_no_publish():
    handler, _dispatcher, publisher, logger = _handler()
    payload = {
        "type": "agent.request.executor",
        "correlation_id": "c1",
        "conv_id": "v1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            # message_id omitted -> validation fails; no steps key here
        },
    }
    await handler.handle_step_batch(payload)

    publisher.publish_status.assert_not_awaited()
    assert logger.error.called


# --- boundary: validation fails, non-dict step element skipped, dict one published (180) ---
@pytest.mark.asyncio
async def test_validation_error_skips_non_dict_step_element():
    handler, _dispatcher, publisher, _logger = _handler()
    payload = {
        "type": "agent.request.executor",
        # message_id omitted -> validation fails
        "correlation_id": "c1",
        "conv_id": "v1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": ["garbage", {"id": "real_step", "execute_by": TOOL_ID}],
        },
    }
    await handler.handle_step_batch(payload)

    # Only the dict step produces a published error event.
    assert publisher.publish_status.await_count == 1
    published_input = publisher.publish_status.await_args_list[0].args[0]
    assert published_input.step.id == "real_step"
    assert published_input.step.status == StepStatus.ERROR
    assert "Validation failed" in published_input.step.error


# --- edge: validation-error step with missing id falls back to 'unknown' (181) ---
@pytest.mark.asyncio
async def test_validation_error_step_missing_id_defaults_unknown():
    handler, _dispatcher, publisher, _logger = _handler()
    payload = {
        "type": "agent.request.executor",
        "correlation_id": "c1",
        "conv_id": "v1",
        "payload": {
            "from": "agent.business",
            "to": "executor.runtime",
            "type": "step_batch",
            "steps": [{"execute_by": TOOL_ID}],  # no id, and message_id missing at top
        },
    }
    await handler.handle_step_batch(payload)

    published_input = publisher.publish_status.await_args_list[0].args[0]
    assert published_input.step.id == "unknown"
