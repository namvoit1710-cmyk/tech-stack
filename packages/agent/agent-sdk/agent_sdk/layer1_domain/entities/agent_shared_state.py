from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

from dataclasses import field
from datetime import datetime

from agent_sdk.layer1_domain.entities.conversation_context import MessageHistory


class MetadataStatus(str, Enum):
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED_FOR_HITL = "PAUSED_FOR_HITL"
    # NOTE (SA-892): there is deliberately no CANCELLED here. A conversation is not
    # a cancellable entity — a Stop cancels the current message's Tasks
    # (``TaskStatus.CANCELLED``), not the conversation. The earlier conversation-
    # status stop mechanism was retired before merge and never shipped, so no
    # persisted conversation carries a CANCELLED status.


class Metadata(BaseModel):
    conv_id: str = Field(
        ..., description="Unique ID for the conversation (replaces thread_id)"
    )
    main_conv_id: Optional[str] = Field(
        None, description="If this is a sub-chat, link to main_conv_id"
    )
    checkpoint_id: str = Field(
        ..., description="Identifier of the current state (e.g. CP-06)"
    )
    parent_checkpoint: Optional[str] = Field(
        None, description="Previous checkpoint used for rollback"
    )

    # Serve Tracing from FE Event
    last_event_id: Optional[str] = Field(
        None, description="ID of the last event processed"
    )
    correlation_id: Optional[str] = Field(
        None, description="Trace ID for full request lifecycle"
    )

    branch: Optional[str] = Field(default="main", description="Execution branch")
    current_agent: Optional[str] = Field(
        None, description="Agent currently holding control"
    )
    status: MetadataStatus = Field(
        default=MetadataStatus.IN_PROGRESS,
        description="Session status: IN_PROGRESS, COMPLETED, FAILED, PAUSED_FOR_HITL",
    )
    current_task_id: Optional[str] = Field(None, description="Current task ID")
    current_run_id: Optional[str] = Field(
        None, description="Current run ID for the active execution"
    )
    current_node_id: Optional[str] = None
    current_message_id: str = ""


# GLOBAL CONTEXT: Shared global context (cross-cutting data)
class GlobalContext(BaseModel):
    conversation_context: List[MessageHistory] = field(default_factory=list)
    knowledge_base_id: str = ""


# TASK (inside Executions): Represents the flattened graph (Flatten Execution Plan - DAG)


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    ERROR = "error"
    FAIL = "fail"
    # SA-892 (task-scoped stop): a Stop cancels the CURRENT MESSAGE's in-flight
    # tasks by marking them CANCELLED. The business-agent then drops any late
    # continuation whose OWN task is CANCELLED — the per-task mirror of the
    # workflow engine's CANCELLED short-circuit (replaces the conversation-status
    # stop mechanism).
    CANCELLED = "cancelled"


class Task(BaseModel):
    id: str = Field(..., alias="task_id", description="Task identifier (e.g. task_001)")
    execute_by: Optional[str] = Field(
        ...,
        alias="agent",
        description="Worker Agent assigned (e.g. tool.search_web, agent.file)",
    )
    goal: str = Field(..., alias="name", description="Task objective")

    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="pending, processing, success, error",
    )

    # SA-892 (conversation_context source): the task→message link is NOT stored on
    # the Task — it lives in ``conversation_context`` (each MessageHistory owns the
    # list of its TaskHistory), written by ``upsert_task_histories`` at plan time.
    # Stop scopes the cancel via that index; a late result is dropped by looking up
    # ITS OWN task status (CANCELLED).

    # Combine both DAG and Sequence from file Plan Events
    parent_task_id: Optional[str] = Field(None, description="Parent task ID")
    next_task_id: Optional[str] = Field(
        None, description="Next task in sequence (Linked list approach)"
    )
    depend_on: List[str] = Field(default_factory=list, description="DAG dependencies")
    is_dynamic: bool = Field(default=False, description="Generated at runtime?")

    input_data: Optional[Dict[str, Any]] = Field(
        None, description="Actual payload passed in"
    )
    output_data: Optional[Dict[str, Any]] = Field(
        None, description="Actual payload out"
    )
    error_detail: Optional[str] = Field(None, description="Error logs")
    workflow_id: Optional[str] = Field(None, description="Workflow ID")
    agent_id: Optional[str] = Field(None, description="Agent ID")
    model_config = {"populate_by_name": True}


# HISTORY: Conversation history or audit log
class History(BaseModel):
    role: str = Field(..., description="Role: user, assistant, system, tool")
    content: str = Field(..., description="Conversation content or log message")
    agent_id: Optional[str] = Field(
        None, description="Name of the Agent that emitted this message/log"
    )
    timestamp: datetime = Field(
        default_factory=_utc_now, description="Recorded timestamp"
    )
    conv_id: str = Field(..., description="ID of the conversation")
    sub_conv_id: str = Field(..., description="ID of the sub-conversation")


# AUDIT LOG ENTRY: Maps to AIW_AGENT_AUDIT_LOGS table (append-only)


class AuditLogStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    WARNING = "WARNING"


class AuditLogEntry(BaseModel):
    id: str = Field(..., description="Primary key UUID of the log entry")
    conv_id: str = Field(..., description="Foreign key linking to Agent_States")
    checkpoint_id: str = Field(
        ..., description="Checkpoint state ID at this moment (e.g. CP-02)"
    )
    agent_name: str = Field(
        ..., description="Agent that performed the action (e.g. OrchestratorAgent)"
    )
    action_name: str = Field(
        ..., description="Specific action name (e.g. Parse_Excel_Header)"
    )
    status: AuditLogStatus = Field(
        ..., description="Action result: SUCCESS, ERROR, WARNING"
    )
    details: Optional[Dict[str, Any]] = Field(
        None, description="Technical or business details (e.g. error from SAP API)"
    )
    created_at: datetime = Field(
        default_factory=_utc_now, description="Timestamp of the action"
    )


# AGENT SHARED STATE: Heart of the communication flow
class AgentSharedState(BaseModel):
    metadata: Metadata
    context: GlobalContext
    executions: List[Task] = Field(
        default_factory=list, description="Flattened execution plan (Flatten DAG)"
    )

    def update_metadata(self, metadata: Metadata) -> None:
        self.metadata = metadata

    def update_context(self, context: GlobalContext) -> None:
        self.context = context

    def update_executions(self, executions: List[Task]) -> None:
        self.executions = executions

    def upsert_task(self, task: Task) -> None:
        for i, existing in enumerate(self.executions):
            if existing.id == task.id:
                self.executions[i] = task
                return
        self.executions.append(task)

    def update_task(self, task_id: str, **kwargs: Any) -> None:
        """Partial update of a single task's fields by *task_id*."""
        valid_fields = set(Task.model_fields.keys())
        invalid = set(kwargs.keys()) - valid_fields
        if invalid:
            raise ValueError(f"Unknown Task fields: {invalid}")
        for task in self.executions:
            if task.id == task_id:
                for key, value in kwargs.items():
                    setattr(task, key, value)
                return
        raise ValueError(f"Task '{task_id}' not found")

    def add_history(self, entry: History) -> None:
        if self.histories is None:
            self.histories = []
        self.histories.append(entry)


# CONVERSATION: Highest-level lifecycle management
class Conversation(BaseModel):
    conv_id: str = Field(..., description="ID of the conversation")
    shared_state: AgentSharedState = Field(..., description="Shared state")
    user_id: str = Field("Unknown", description="User who owns the conversation")
