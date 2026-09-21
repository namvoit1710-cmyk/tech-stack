from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


QUEUE_SOURCE = "orchestrator"
QUEUE_TARGET = "agent"


class QueueRequestType(str, Enum):
    SUB_USER_CHAT = "sub:user:chat"
    USER_ACTION = "user:action"
    CONVERSATION_PLAN_CREATED = "conversation.plan.created"
    WORKFLOW_INPUT_REQUESTED = "workflow:input:requested"
    WORKFLOW_DATE_EDIT_REQUESTED = "workflow:date_edit:requested"
    MAIN_USER_RERUN = "main:user:rerun"
    WORKFLOW_NODE_STATUS = "workflow:node:status"
    EXECUTOR_STEP_STATUS = "executor.step.status"

@dataclass(slots=True)
class NodeInfo:
    run_id: str
    node_id: str
    node_name: str
    task_id: Optional[str] = None
    status: Optional[str] = None
    message: Optional[str] = None

@dataclass(slots=True)
class QueueStep:
    id: str
    execute_by: str
    goal: str
    message: str
    uploaded_file_ids: list[str] = field(default_factory=list)
    input_schema: list[str] = field(default_factory=list)
    output_schema: list[str] = field(default_factory=list)
    next_step_id: Optional[str] = None


@dataclass(slots=True)
class QueuePayload:
    from_: str
    to: str
    steps: list[QueueStep] = field(default_factory=list)
    node_info: Optional[NodeInfo] = None
    workflow_id: Optional[str] = None


@dataclass(slots=True)
class QueueEvent:
    id: str
    type: str
    conv_id: str
    payload: Optional[QueuePayload] = None
    metadata: Optional[dict[str, Any]] = None
    correlation_id: str = ""


PLAN_EVENTS = {
    QueueRequestType.CONVERSATION_PLAN_CREATED,
    QueueRequestType.SUB_USER_CHAT,
    QueueRequestType.WORKFLOW_INPUT_REQUESTED,
    QueueRequestType.MAIN_USER_RERUN,
    QueueRequestType.WORKFLOW_NODE_STATUS
}

REVIEW_EVENTS = {
    QueueRequestType.EXECUTOR_STEP_STATUS,
}
    