from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from app.layer1_domain.entities.queue_event import (
    QueueRequestType,
    QueueStep,
    QueuePayload,
    QueueEvent,
    QueuePayloadType,
)
from app.layer1_domain.entities.workflow_event import WorkflowEvent
from app.layer1_domain.entities.ui_event import UIActionEvent


@dataclass
class NodeInfo:
    run_id: str
    node_id: str
    node_name: str
    status: Optional[str] = None
    message: Optional[str] = None


@dataclass
class StepDTO:
    id: str
    execute_by: str
    goal: str
    message: str
    status: TaskStatus

    next_step_id: Optional[str] = None
    uploaded_file_ids: List[str] = field(default_factory=list)

    input_data: Optional[Dict[str, Any]] = None
    output_data: Optional[Dict[str, Any]] = None
    error: str = ""

@dataclass
class PayloadDTO:
    from_: str
    to: str

    steps: List[StepDTO] = field(default_factory=list)
    node_info: Optional[NodeInfo] = None
    workflow: Optional[WorkflowEvent] = None
    workflow_id: Optional[str] = None
    type: Optional[str] = None
    batch_id: Optional[str] = None

@dataclass
class FileSummaryOutputDTO:
    file_id: str
    content_type: str
    summary: str
    raw_output: Dict[str, Any] = field(default_factory=dict)
    rag: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_id": self.file_id,
            "content_type": self.content_type,
            "summary": self.summary,
            "raw_output": self.raw_output,
            "rag": self.rag,
        }

@dataclass
class EventDTO:
    id: str
    type: QueueRequestType
    conv_id: str
    correlation_id: str

    payload: Optional[PayloadDTO] = None
    metadata: Optional[dict] = None
    # SA-892 (task-scoped stop): origin message/turn id carried by a self-published
    # CONVERSATION_PLAN_CREATED continuation, used by the stop guard to drop a stale
    # continuation whose current_task_id has advanced to a newer message's task.
    origin_message_id: Optional[str] = None
    # ui_action_event: Optional[UIActionEvent] = None

    # -------------------------
    # DTO → Entity converters
    # -------------------------
    @classmethod
    def from_dict(cls, data: dict) -> "EventDTO":
        payload = data.get("payload")
        if isinstance(payload, dict):
            payload = cls.payload_dto_from_dict(payload)

        return cls(
            id=data.get("id") or data.get("correlation_id") or "",
            type=QueueRequestType(data["type"]),
            conv_id=data.get("conv_id", ""),
            payload=payload,
            metadata=data.get("metadata"),
            correlation_id=data.get("correlation_id", ""),
            origin_message_id=data.get("origin_message_id"),
        )
    
    @staticmethod
    def step_dto_from_dict(data: dict) -> StepDTO:
        return StepDTO(
            id=data["id"],
            execute_by=data.get("execute_by"),
            goal=data.get("goal", ""),
            message=data.get("message", ""),
            status=data.get("status", TaskStatus.PENDING),
            next_step_id=data.get("next_step_id"),
            uploaded_file_ids=list(data.get("uploaded_file_ids") or []),
            input_data=data.get("input_data") if isinstance(data.get("input_data"), dict) else {},
            output_data=data.get("output_data") if isinstance(data.get("output_data"), dict) else {},
            error=str(data.get("error") or ""),
        )

    @staticmethod
    def payload_dto_from_dict(data: dict) -> PayloadDTO:
        node_info = data.get("node_info")
        if isinstance(node_info, dict):
            node_info = NodeInfo(
                run_id=node_info.get("run_id", ""),
                node_id=node_info.get("node_id", ""),
                node_name=node_info.get("node_name", ""),
                status=node_info.get("status"),
                message=node_info.get("message"),
            )

        workflow = data.get("workflow")
        if isinstance(workflow, dict):
            workflow = WorkflowEvent.from_dict(workflow)

        return PayloadDTO(
            from_=data.get("from", ""),
            to=data.get("to", ""),
            steps=[
                EventDTO.step_dto_from_dict(step)
                for step in data.get("steps", [])
                if isinstance(step, dict)
            ],
            node_info=node_info if isinstance(node_info, NodeInfo) else None,
            workflow=workflow if isinstance(workflow, WorkflowEvent) else None,
            workflow_id=data.get("workflow_id"),
            type=data.get("type"),
            batch_id=data.get("batch_id"),
        )

    @staticmethod
    def step_dto_to_entity(dto: StepDTO) -> QueueStep:
        return QueueStep(
            id=dto.id,
            execute_by=dto.execute_by,
            next_step_id=dto.next_step_id,
            goal=dto.goal,
            status=dto.status,
            message=dto.message,
            uploaded_file_ids=dto.uploaded_file_ids,
            input_data=dto.input_data,
            output_data=dto.output_data,
        )

    @staticmethod
    def node_info_dto_to_entity(dto: Optional[NodeInfo]):
        if dto is None:
            return None

        from app.layer1_domain.entities.queue_event import (
            NodeInfo as EntityNodeInfo,
        )

        return EntityNodeInfo(
            run_id=dto.run_id,
            node_id=dto.node_id,
            node_name=dto.node_name,
            status=dto.status,
            message=dto.message,
        )

    @staticmethod
    def payload_dto_to_entity(dto: PayloadDTO) -> QueuePayload:
        node_info_entity = EventDTO.node_info_dto_to_entity(dto.node_info)

        workflow_id = getattr(dto, "workflow_id", None)

        return QueuePayload(
            from_=dto.from_,
            to=dto.to,
            steps=[
                EventDTO.step_dto_to_entity(step)
                for step in dto.steps
            ],
            node_info=node_info_entity,
            workflow_id=workflow_id,
            type=getattr(dto, "type", None) or QueuePayloadType.STEP_BATCH,
            batch_id=getattr(dto, "batch_id", None),
        )

    def to_entity(self) -> QueueEvent:
        payload = (
            self.payload_dto_to_entity(self.payload)
            if self.payload
            else None
        )

        return QueueEvent(
            message_id=self.id,
            type=self.type,
            conv_id=self.conv_id,
            payload=payload,
            metadata=self.metadata,
            correlation_id=self.correlation_id,
        )

    @staticmethod
    def workflow_event_to_dto(event: WorkflowEvent) -> "EventDTO":
        payload = PayloadDTO(
            from_="",
            to="",
            workflow=event,
            node_info=(
                NodeInfo(
                    run_id=event.run_id,
                    node_id=event.node_id,
                    node_name=event.node.name if event.node else "",
                    status=event.status,
                    message=event.metadata.get("message")
                    if event.metadata
                    else None,
                )
                if event.node_id
                else None
            ),
        )

        return EventDTO(
            id=event.event_id,
            type=QueueRequestType(event.event_type),
            conv_id="",
            payload=payload,
            metadata=event.metadata,
            correlation_id=(
                event.run.correlation_id
                if event.run
                else ""
            ),
        )
