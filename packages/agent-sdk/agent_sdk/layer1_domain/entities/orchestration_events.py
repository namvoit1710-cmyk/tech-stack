from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any

from agent_sdk.layer1_domain.entities.event_envelope import EventEnvelope
from agent_sdk.layer1_domain.entities.executor_step_contract import (
    ExecutorStepEventPayload,
)


class OrchestrationEventType(str, Enum):
    CONVERSATION_PLAN_CREATED = "conversation.plan.created"
    AGENT_PLAN_CREATED = "agent.plan.created"
    AGENT_PLAN_EXECUTING = "agent.plan.executing"
    AGENT_PLAN_EXECUTED = "agent.plan.executed"
    AGENT_REQUEST_AGENT = "agent.request.agent"
    AGENT_PLAN_SUCCESS = "agent.plan.success"
    AGENT_PLAN_ERROR = "agent.plan.error"
    EXECUTOR_REQUEST_STEP_BATCH = "executor.request.step_batch"
    EXECUTOR_REQUEST_AGENT = "executor.request.agent"
    AGENT_STEP_STATUS = "agent.step.status"
    EXECUTOR_STEP_STATUS = "executor.step.status"


CONVERSATION_PLAN_CREATED = OrchestrationEventType.CONVERSATION_PLAN_CREATED.value
AGENT_PLAN_CREATED = OrchestrationEventType.AGENT_PLAN_CREATED.value
AGENT_PLAN_EXECUTING = OrchestrationEventType.AGENT_PLAN_EXECUTING.value
AGENT_PLAN_EXECUTED = OrchestrationEventType.AGENT_PLAN_EXECUTED.value
AGENT_REQUEST_AGENT = OrchestrationEventType.AGENT_REQUEST_AGENT.value
AGENT_PLAN_SUCCESS = OrchestrationEventType.AGENT_PLAN_SUCCESS.value
AGENT_PLAN_ERROR = OrchestrationEventType.AGENT_PLAN_ERROR.value
EXECUTOR_REQUEST_STEP_BATCH = OrchestrationEventType.EXECUTOR_REQUEST_STEP_BATCH.value
EXECUTOR_REQUEST_AGENT = OrchestrationEventType.EXECUTOR_REQUEST_AGENT.value
AGENT_STEP_STATUS = OrchestrationEventType.AGENT_STEP_STATUS.value
EXECUTOR_STEP_STATUS = OrchestrationEventType.EXECUTOR_STEP_STATUS.value


@dataclass
class StepDefinition:
    step_id: str = ""
    title: str = ""
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskDefinition:
    task_id: str = ""
    name: str = ""
    description: str = ""
    steps: list[StepDefinition] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


def _build_event(
    event_type: str,
    conversation_id: str,
    payload: Any,
    *,
    message_id: str = "",
    correlation_id: str = "",
) -> EventEnvelope:
    return EventEnvelope(
        type=event_type,
        message_id=message_id,
        correlation_id=correlation_id,
        conversation_id=conversation_id,
        payload=_serialize(payload),
    )


def create_conversation_plan_created_event(
    conversation_id: str, tasks: list[TaskDefinition]
) -> EventEnvelope:
    return _build_event(CONVERSATION_PLAN_CREATED, conversation_id, {"tasks": tasks})


def create_agent_plan_created_event(
    conversation_id: str, task: TaskDefinition
) -> EventEnvelope:
    return _build_event(AGENT_PLAN_CREATED, conversation_id, {"task": task})


def create_agent_plan_executing_event(
    conversation_id: str, step: StepDefinition
) -> EventEnvelope:
    return _build_event(AGENT_PLAN_EXECUTING, conversation_id, {"step": step})


def create_agent_plan_executed_event(
    conversation_id: str, step: StepDefinition
) -> EventEnvelope:
    return _build_event(AGENT_PLAN_EXECUTED, conversation_id, {"step": step})


def create_agent_request_event(conversation_id: str, payload: Any) -> EventEnvelope:
    return _build_event(AGENT_REQUEST_AGENT, conversation_id, payload)


def create_agent_plan_success_event(
    conversation_id: str, payload: Any
) -> EventEnvelope:
    return _build_event(AGENT_PLAN_SUCCESS, conversation_id, payload)


def create_agent_plan_error_event(conversation_id: str, payload: Any) -> EventEnvelope:
    return _build_event(AGENT_PLAN_ERROR, conversation_id, payload)


def create_executor_request_step_batch_event(
    conversation_id: str,
    payload: ExecutorStepEventPayload,
    *,
    message_id: str = "",
    correlation_id: str = "",
) -> EventEnvelope:
    return _build_event(
        EXECUTOR_REQUEST_STEP_BATCH,
        conversation_id,
        payload.to_payload(),
        message_id=message_id,
        correlation_id=correlation_id,
    )


def create_executor_request_agent_event(
    conversation_id: str,
    payload: ExecutorStepEventPayload,
    *,
    message_id: str = "",
    correlation_id: str = "",
) -> EventEnvelope:
    return _build_event(
        EXECUTOR_REQUEST_AGENT,
        conversation_id,
        payload.to_payload(),
        message_id=message_id,
        correlation_id=correlation_id,
    )


def create_agent_step_status_event(
    conversation_id: str,
    payload: ExecutorStepEventPayload,
    *,
    message_id: str = "",
    correlation_id: str = "",
) -> EventEnvelope:
    return _build_event(
        AGENT_STEP_STATUS,
        conversation_id,
        payload.to_payload(),
        message_id=message_id,
        correlation_id=correlation_id,
    )


def create_executor_step_status_event(
    conversation_id: str,
    payload: ExecutorStepEventPayload,
    *,
    message_id: str = "",
    correlation_id: str = "",
) -> EventEnvelope:
    return _build_event(
        EXECUTOR_STEP_STATUS,
        conversation_id,
        payload.to_payload(),
        message_id=message_id,
        correlation_id=correlation_id,
    )
