from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# =========================================================
# ENUMS
# =========================================================

class QueueRequestType(str, Enum):
    SUB_USER_CHAT = "sub:user:chat"
    USER_ACTION = "user:action"
    CONVERSATION_PLAN_CREATED = "conversation.plan.created"
    WORKFLOW_NODE_INPUT_REQUESTED = "input.requested"
    WORKFLOW_NODE_INPUT_SUBMITTED = "input.submitted"
    MAIN_USER_RERUN = "main:user:rerun"
    EXECUTOR_STEP_STATUS = "executor.step.status"
    AGENT_REQUEST_EXECUTOR = "agent.request.executor"
    WORKFLOW_NODE_OUTPUT_PRODUCED = "node.output.produced"
    WORKFLOW_RUN_STATUS = "run.status"
    WORKFLOW_NODE_DISPATCHED = "node.dispatched"
    WORKFLOW_NODE_COMPLETED = "node.completed"
    WORKFLOW_NODE_AI_FEED = "ai.feed.produced"
    WORKFLOW_NODE_APPROVAL_FLOW_RESULT = "node.approval_flow.result"

   
PLAN_EVENTS = {
    QueueRequestType.CONVERSATION_PLAN_CREATED,
    QueueRequestType.SUB_USER_CHAT,
    QueueRequestType.MAIN_USER_RERUN,
    QueueRequestType.WORKFLOW_RUN_STATUS,
    QueueRequestType.WORKFLOW_NODE_INPUT_REQUESTED,
    QueueRequestType.WORKFLOW_NODE_INPUT_SUBMITTED,
    QueueRequestType.WORKFLOW_NODE_DISPATCHED,
    QueueRequestType.WORKFLOW_NODE_COMPLETED,
    QueueRequestType.WORKFLOW_NODE_AI_FEED,
    QueueRequestType.WORKFLOW_NODE_APPROVAL_FLOW_RESULT,
}

REVIEW_EVENTS = {
    QueueRequestType.EXECUTOR_STEP_STATUS,
}

class EventSource(str, Enum):
    WORKFLOW_CONTROL_PLANE = "workflow-control-plane"
    UI = "ui"


class QueuePayloadType(str, Enum):
    STEP_BATCH = "step_batch"


class QueuePayloadTo(str, Enum):
    EXECUTOR_RUNTIME = "executor.runtime"


# =========================================================
# NODE INFO
# =========================================================

@dataclass
class NodeInfo:
    run_id: str
    node_id: str
    node_name: str

    task_id: str | None = None
    status: str | None = None
    message: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NodeInfo":
        return cls(
            run_id=data["run_id"],
            node_id=data["node_id"],
            node_name=data["node_name"],
            task_id=data.get("task_id"),
            status=data.get("status"),
            message=data.get("message"),
        )
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node_id": self.node_id,
            "node_name": self.node_name,
            "task_id": self.task_id,
            "status": self.status,
            "message": self.message,
        }


# =========================================================
# QUEUE STEP
# =========================================================

@dataclass
class QueueStep:
    id: str
    execute_by: str
    goal: str
    message: str

    next_step_id: str | None = None
    status: str = "PENDING"  # TaskStatus removed → use string or keep enum externally
    uploaded_file_ids: list[str] | None = None

    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueStep":
        return cls(
            id=data["id"],
            execute_by=data["execute_by"],
            goal=data["goal"],
            message=data["message"],
            next_step_id=data.get("next_step_id"),
            status=data.get("status", "PENDING"),
            uploaded_file_ids=data.get("uploaded_file_ids"),
            input_data=data.get("input_data"),
            output_data=data.get("output_data"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "execute_by": self.execute_by,
            "goal": self.goal,
            "message": self.message,
            "next_step_id": self.next_step_id,
            "status": self.status,
            "uploaded_file_ids": self.uploaded_file_ids,
            "input_data": self.input_data,
            "output_data": self.output_data,
        }
# =========================================================
# QUEUE PAYLOAD
# =========================================================

@dataclass
class QueuePayload:
    from_: str
    to: str
    steps: list[QueueStep]

    node_info: NodeInfo | None = None
    workflow_id: str | None = None
    type: QueuePayloadType = QueuePayloadType.STEP_BATCH
    batch_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueuePayload":
        return cls(
            from_=data["from"],
            to=data["to"],
            steps=[QueueStep.from_dict(s) for s in data.get("steps", [])],
            node_info=NodeInfo.from_dict(data["node_info"]) if data.get("node_info") else None,
            workflow_id=data.get("workflow_id"),
            type=QueuePayloadType(data.get("type", QueuePayloadType.STEP_BATCH)),
            batch_id=data.get("batch_id"),
        )
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "from": self.from_,   # 🔥 alias fix here
            "to": self.to,
            "steps": [s.to_dict() for s in self.steps],
            "node_info": self.node_info.to_dict() if self.node_info else None,
            "workflow_id": self.workflow_id,
            "type": self.type.value if hasattr(self.type, "value") else self.type,
            "batch_id": self.batch_id,
        }


# =========================================================
# QUEUE EVENT
# =========================================================

@dataclass
class QueueEvent:
    message_id: str
    type: str
    conv_id: str
    correlation_id: str

    payload: QueuePayload | None = None
    metadata: dict | None = None
    # SA-892 (task-scoped stop): the origin message/turn id a self-published
    # continuation (CONVERSATION_PLAN_CREATED) is continuing. The stop guard uses
    # it to detect a STALE continuation whose Metadata.current_task_id has since
    # advanced to a newer message's task (see CreatePlanUseCase._incoming_task_is_cancelled).
    origin_message_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueEvent":
        return cls(
            message_id=data["message_id"],
            type=data["type"],
            conv_id=data["conv_id"],
            correlation_id=data["correlation_id"],
            payload=QueuePayload.from_dict(data["payload"]) if data.get("payload") else None,
            metadata=data.get("metadata"),
            origin_message_id=data.get("origin_message_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "type": self.type,
            "conv_id": self.conv_id,
            "correlation_id": self.correlation_id,
            "payload": self.payload.to_dict() if self.payload else None,
            "metadata": self.metadata,
            "origin_message_id": self.origin_message_id,
        }
