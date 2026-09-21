"""
Validate Step Batch Use Case.

Validates the inbound agent.request.executor envelope shape.
Pure validation logic — no I/O, no side effects.
"""

from __future__ import annotations

from typing import Any
import re

from app.layer1_domain.entities.batch_request import StepBatchRequest
from app.layer1_domain.entities.step import Step

EVENT_TYPE = "agent.request.executor"
PAYLOAD_TYPE = "step_batch"
EXECUTOR_RUNTIME = "executor.runtime"
EXECUTE_BY_PATTERN = re.compile(
    r"^(?:tool|agent)\."
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)


class ValidationError(Exception):
    def __init__(self, reason: str, step_id: str | None = None):
        self.reason = reason
        self.step_id = step_id
        super().__init__(reason)


def validate_step_batch(raw: dict[str, Any]) -> StepBatchRequest:
    """
    Validate the raw envelope and return a parsed StepBatchRequest.

    Raises ValidationError if the contract is violated.
    """
    # type must be agent.request.executor
    msg_type = raw.get("type") or ""
    if msg_type != EVENT_TYPE:
        raise ValidationError(f"Expected type '{EVENT_TYPE}', got '{msg_type}'")

    # message_id required
    message_id = raw.get("message_id") or ""
    if not message_id:
        raise ValidationError("message_id is required")

    # correlation_id required
    correlation_id = raw.get("correlation_id") or ""
    if not correlation_id:
        raise ValidationError("correlation_id is required")

    # conv_id required
    conv_id = raw.get("conv_id") or ""
    if not conv_id:
        raise ValidationError("conv_id is required")

    # payload.steps must be a non-empty list
    payload = raw.get("payload")
    if not isinstance(payload, dict):
        raise ValidationError("payload must be an object")

    payload_type = payload.get("type") or ""
    if payload_type != PAYLOAD_TYPE:
        raise ValidationError(f"payload.type must be '{PAYLOAD_TYPE}'")

    from_agent = payload.get("from") or ""
    if not from_agent:
        raise ValidationError("payload.from is required")

    to_agent = payload.get("to") or ""
    if to_agent != EXECUTOR_RUNTIME:
        raise ValidationError(f"payload.to must be '{EXECUTOR_RUNTIME}'")

    steps_raw = payload.get("steps")
    if not isinstance(steps_raw, list) or len(steps_raw) == 0:
        raise ValidationError("payload.steps must contain at least one step")

    # Every step must have id and execute_by
    steps: list[Step] = []
    for i, step_raw in enumerate(steps_raw):
        if not isinstance(step_raw, dict):
            raise ValidationError(f"payload.steps[{i}] must be an object")

        step_id = step_raw.get("id") or ""
        if not step_id:
            raise ValidationError(f"payload.steps[{i}].id is required", step_id=None)

        execute_by = step_raw.get("execute_by") or ""
        if not execute_by:
            raise ValidationError(
                f"Step '{step_id}' is missing execute_by", step_id=step_id
            )
        if not _is_supported_execute_by(execute_by):
            raise ValidationError(
                (
                    f"Step '{step_id}' execute_by must be 'tool.<tool_id>' "
                    f"or 'agent.<agent_id>'"
                ),
                step_id=step_id,
            )

        input_data = step_raw.get("input_data")
        if input_data is None:
            input_data = {}
        if not isinstance(input_data, dict):
            raise ValidationError(
                f"Step '{step_id}' input_data must be an object",
                step_id=step_id,
            )

        output_data = step_raw.get("output_data")
        if output_data is None:
            output_data = {}
        if not isinstance(output_data, dict):
            raise ValidationError(
                f"Step '{step_id}' output_data must be an object",
                step_id=step_id,
            )

        steps.append(
            Step(
                id=step_id,
                execute_by=execute_by,
                input_data=input_data,
                goal=step_raw.get("goal", ""),
                message=step_raw.get("message", ""),
                output_data=output_data,
                next_step_id=step_raw.get("next_step_id"),
                uploaded_file_ids=step_raw.get("uploaded_file_ids") or [],
            )
        )

    batch_id = payload.get("batch_id") or message_id

    return StepBatchRequest(
        message_id=message_id,
        correlation_id=correlation_id,
        conv_id=conv_id,
        batch_id=batch_id,
        from_agent=from_agent,
        to_agent=to_agent,
        steps=steps,
    )


def _is_supported_execute_by(execute_by: str) -> bool:
    return bool(EXECUTE_BY_PATTERN.fullmatch(execute_by))
