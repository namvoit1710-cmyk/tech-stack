"""Use case that returns the complete Socket.IO event catalog.

Clients call ``GET /socketio/config`` to discover every event they can
listen to, the payload schema for each event, and the room subscription
instructions needed to receive them.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain import events as ev
from app.layer1_domain.events import EventTopic

_PYTHON_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}

# Base fields from DomainEvent — excluded from per-event schemas
_BASE_FIELD_NAMES = {f.name for f in dataclasses.fields(ev.DomainEvent)}


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class EventFieldSchema:
    name: str = ""
    type: str = "string"
    description: str = ""
    example: Any = None


@dataclass
class SocketEventDefinition:
    event: str = ""
    category: str = ""
    description: str = ""
    room: str = ""
    fields: list[EventFieldSchema] = field(default_factory=list)


@dataclass
class RoomDefinition:
    room_pattern: str = ""
    join_event: str = ""
    leave_event: str = ""
    join_payload: dict = field(default_factory=dict)
    description: str = ""


@dataclass
class GetSocketioConfigCommand:
    pass


@dataclass
class GetSocketioConfigResult:
    events: list[SocketEventDefinition] = field(default_factory=list)
    rooms: list[RoomDefinition] = field(default_factory=list)
    meta_fields: list[EventFieldSchema] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Static event catalog
# ---------------------------------------------------------------------------

_EVENT_DESCRIPTIONS: dict[EventTopic, str] = {
    EventTopic.RUN_STATUS_CHANGED: "Emitted on every run-lifecycle transition; the payload 'status' field is one of started / completed / failed / cancelled / paused / resumed / suspended / pending.",
    EventTopic.NODE_DISPATCHED: "Emitted when a node is dispatched for execution — first run, retry, resume, rerun, or manual trigger (see dispatch_reason).",
    EventTopic.NODE_COMPLETED: "Emitted when a node completes successfully.",
    EventTopic.NODE_FAILED: "Emitted when a node fails after exhausting retries.",
    EventTopic.NODE_CANCELLED: "Emitted when a node is cancelled or skipped (see reason).",
    EventTopic.INPUT_REQUESTED: "Emitted when a pending-user node (INPUT / FILE_UPLOAD / HUMAN_ACTION / DATA_EDIT) pauses the run; payload 'pending_kind' discriminates.",
    EventTopic.INPUT_SUBMITTED: "Emitted when the user responds to a pending node; payload 'pending_kind' discriminates.",
    EventTopic.NODE_APPROVAL_FLOW: "Emitted when an APPROVAL_FLOW node returns its transformed approval graph result.",
    EventTopic.LOOP_ITERATION_COMPLETED: "Emitted after each iteration of a LOOP node completes.",
    EventTopic.CHILD_RUN_STARTED: "Emitted when a WORKFLOW node starts a child workflow run.",
    EventTopic.CHILD_RUN_COMPLETED: "Emitted when a child workflow run finishes successfully.",
    EventTopic.CHILD_RUN_FAILED: "Emitted when a child workflow run terminates on error.",
    EventTopic.CHILD_RUN_CANCELLED: "Emitted when a child workflow run is cancelled.",
    EventTopic.CONTEXT_UPDATED: "Emitted when a node's output is stored in the run context.",
    EventTopic.EDGE_TRAVERSED: "Emitted when execution traverses an edge from one node to another.",
    EventTopic.ARTIFACT_ADDED: "Emitted when an artifact file is attached to a node's output.",
    EventTopic.NODE_INPUT_EDITED: "Emitted when a node's input data is edited (CSV patch or file swap).",
}

_EVENT_CATEGORIES: dict[EventTopic, str] = {
    EventTopic.RUN_STATUS_CHANGED: "run",
    EventTopic.NODE_DISPATCHED: "node",
    EventTopic.NODE_COMPLETED: "node",
    EventTopic.NODE_FAILED: "node",
    EventTopic.NODE_CANCELLED: "node",
    EventTopic.INPUT_REQUESTED: "input",
    EventTopic.INPUT_SUBMITTED: "input",
    EventTopic.NODE_APPROVAL_FLOW: "node",
    EventTopic.LOOP_ITERATION_COMPLETED: "node",
    EventTopic.CHILD_RUN_STARTED: "child_run",
    EventTopic.CHILD_RUN_COMPLETED: "child_run",
    EventTopic.CHILD_RUN_FAILED: "child_run",
    EventTopic.CHILD_RUN_CANCELLED: "child_run",
    EventTopic.CONTEXT_UPDATED: "context",
    EventTopic.EDGE_TRAVERSED: "edge",
    EventTopic.ARTIFACT_ADDED: "artifact",
    EventTopic.NODE_INPUT_EDITED: "node_input",
}

# Maps EventTopic → domain event dataclass
_EVENT_DATACLASS_MAP: dict[EventTopic, type] = {
    EventTopic.RUN_STATUS_CHANGED: ev.RunStatusChanged,
    EventTopic.NODE_DISPATCHED: ev.NodeDispatched,
    EventTopic.NODE_COMPLETED: ev.NodeCompleted,
    EventTopic.NODE_FAILED: ev.NodeFailed,
    EventTopic.NODE_CANCELLED: ev.NodeCancelled,
    EventTopic.INPUT_REQUESTED: ev.InputRequested,
    EventTopic.INPUT_SUBMITTED: ev.InputSubmitted,
    EventTopic.NODE_APPROVAL_FLOW: ev.NodeApprovalFlow,
    EventTopic.LOOP_ITERATION_COMPLETED: ev.LoopIterationCompleted,
    EventTopic.CHILD_RUN_STARTED: ev.ChildRunStarted,
    EventTopic.CHILD_RUN_COMPLETED: ev.ChildRunCompleted,
    EventTopic.CHILD_RUN_FAILED: ev.ChildRunFailed,
    EventTopic.CHILD_RUN_CANCELLED: ev.ChildRunCancelled,
    EventTopic.CONTEXT_UPDATED: ev.ContextUpdated,
    EventTopic.EDGE_TRAVERSED: ev.EdgeTraversed,
    EventTopic.ARTIFACT_ADDED: ev.ArtifactAdded,
    EventTopic.NODE_INPUT_EDITED: ev.NodeInputEdited,
}

# Which events use parent_run_id as the room key instead of run_id
_PARENT_RUN_EVENTS = {
    EventTopic.CHILD_RUN_STARTED,
    EventTopic.CHILD_RUN_COMPLETED,
    EventTopic.CHILD_RUN_FAILED,
    EventTopic.CHILD_RUN_CANCELLED,
}

# Field-level descriptions for well-known field names
_FIELD_DESCRIPTIONS: dict[str, str] = {
    "run_id": "Unique identifier of the workflow run.",
    "workflow_id": "Identifier of the workflow definition.",
    "task_id": "Unique identifier of the task.",
    "node_id": "Identifier of the workflow node that produced this event.",
    "worker_type": "Type of worker that handles this task (e.g. 'http-request').",
    "input": "Resolved input data sent to the worker.",
    "node_config": "Static configuration of the node.",
    "idempotency_key": "Key for idempotent task dispatch.",
    "correlation_id": "Correlation identifier for tracing across services.",
    "callback_topic": "Messaging topic for async callbacks.",
    "status": "Final status of the run or task.",
    "output_data": "Output data produced by the run.",
    "error": "Error message describing the failure.",
    "error_code": "Machine-readable error code.",
    "error_message": "Human-readable error message.",
    "failed_task_id": "Identifier of the task that caused the run failure.",
    "cancelled_by": "User or system that cancelled the run.",
    "reason": "Reason for cancellation or skipping.",
    "attempt": "Current retry attempt number (1-based).",
    "next_retry_at": "ISO-8601 timestamp of the next retry attempt.",
    "execution_time_ms": "Task execution duration in milliseconds.",
    "previous_status": "Status of the task before re-run.",
    "input_schema": "Array of form field definitions the UI should render.",
    "instruction": "Human-readable instruction for the user action.",
    "action": "User action taken: 'confirm' or 'reject'.",
    "expression": "The expression that was evaluated.",
    "result": "Evaluation result. CONDITION: True/False. SWITCH: True when a rule matched, False otherwise.",
    "branch": "Branch taken. CONDITION: 'then'/'else'. SWITCH: matched branch name (or '' when no rule matched).",
    "change_request_id": "Change request identifier used to retrieve the approval process.",
    "nodes": "Approval flow graph nodes produced by the transformer.",
    "edges": "Approval flow graph edges produced by the transformer.",
    "output_port": "Port used to route downstream execution: 'success' or 'failure'.",
    "iteration": "Current loop iteration index (0-based).",
    "total": "Total number of loop iterations.",
    "parent_run_id": "Identifier of the parent workflow run.",
    "parent_node_id": "Node in the parent run that spawned the child.",
    "child_run_id": "Identifier of the child workflow run.",
    "child_workflow_id": "Workflow definition identifier for the child run.",
    "node_name": "Human-readable name of the node.",
    "data_type": "Storage type of the context entry: 'inline' or 'file_ref'.",
}

_FIELD_EXAMPLES: dict[str, Any] = {
    "run_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "workflow_id": "wf-order-processing",
    "task_id": "task-001",
    "node_id": "node-api-call",
    "worker_type": "http-request",
    "input": {"url": "https://api.example.com/data", "method": "GET"},
    "node_config": {"timeout": 30},
    "idempotency_key": "run-123_node-api-call_1",
    "correlation_id": "corr-xyz-789",
    "callback_topic": "workflow/tasks/complete",
    "status": "COMPLETED",
    "output_data": {"result": "success"},
    "error": "Connection timeout after 30s",
    "error_code": "WORKER_TIMEOUT",
    "error_message": "Worker did not respond within 30 seconds.",
    "failed_task_id": "task-001",
    "cancelled_by": "user@example.com",
    "reason": "branch_not_taken",
    "attempt": 2,
    "next_retry_at": "2025-01-01T12:00:05Z",
    "execution_time_ms": 1250,
    "previous_status": "FAILED",
    "input_schema": [{"key": "email", "type": "string", "label": "Email"}],
    "instruction": "Please review and approve the order.",
    "action": "confirm",
    "expression": "{{$api_call.status_code}} == 200",
    "result": True,
    "branch": "then",
    "change_request_id": "CR-123456",
    "nodes": [{"id": "start_node", "role": "Start", "name": None, "status": "None"}],
    "edges": [{"source": "start_node", "target": "1"}],
    "output_port": "success",
    "iteration": 3,
    "total": 10,
    "parent_run_id": "parent-run-001",
    "parent_node_id": "node-child-wf",
    "child_run_id": "child-run-001",
    "child_workflow_id": "wf-sub-process",
    "node_name": "API Call",
    "data_type": "file_ref",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _python_type_to_str(tp: type) -> str:
    origin = getattr(tp, "__origin__", None)
    if origin is list:
        return "array"
    if origin is dict:
        return "object"
    return _PYTHON_TYPE_MAP.get(tp, "string")


def _build_fields_for_event(event_cls: type) -> list[EventFieldSchema]:
    fields: list[EventFieldSchema] = []
    for f in dataclasses.fields(event_cls):
        if f.name in _BASE_FIELD_NAMES:
            continue
        fields.append(EventFieldSchema(
            name=f.name,
            type=_python_type_to_str(f.type),
            description=_FIELD_DESCRIPTIONS.get(f.name, ""),
            example=_FIELD_EXAMPLES.get(f.name),
        ))
    return fields


def _build_event_definitions() -> list[SocketEventDefinition]:
    definitions: list[SocketEventDefinition] = []
    for event_name, event_cls in _EVENT_DATACLASS_MAP.items():
        room = "run:{parent_run_id}" if event_name in _PARENT_RUN_EVENTS else "run:{run_id}"
        definitions.append(SocketEventDefinition(
            event=event_name,
            category=_EVENT_CATEGORIES[event_name],
            description=_EVENT_DESCRIPTIONS.get(event_name, ""),
            room=room,
            fields=_build_fields_for_event(event_cls),
        ))
    return definitions


def _build_room_definitions() -> list[RoomDefinition]:
    return [
        RoomDefinition(
            room_pattern="run:{run_id}",
            join_event="join:run",
            leave_event="leave:run",
            join_payload={"run_id": "<run_id>"},
            description=(
                "Subscribe to all events for a specific workflow run. "
                "Receives task lifecycle, human interaction, node execution, "
                "and context update events."
            ),
        ),
        # RoomDefinition(
        #     room_pattern="workflow:{workflow_id}",
        #     join_event="join:workflow",
        #     leave_event="leave:workflow",
        #     join_payload={"workflow_id": "<workflow_id>"},
        #     description=(
        #         "Subscribe to all events for any run of a specific workflow. "
        #         "Useful for monitoring dashboards that track all executions "
        #         "of a workflow definition."
        #     ),
        # ),
    ]


def _build_meta_fields() -> list[EventFieldSchema]:
    return [
        EventFieldSchema(
            name="_event",
            type="string",
            description="Normalized event name (same as the Socket.IO event key). Auto-added to every payload.",
            example="node.completed",
        ),
        EventFieldSchema(
            name="_timestamp",
            type="string",
            description="ISO-8601 UTC timestamp of when the event was emitted via Socket.IO. Auto-added to every payload.",
            example="2025-03-28T10:15:30.123456+00:00",
        ),
        EventFieldSchema(
            name="event_id",
            type="string",
            description="Unique identifier for this event instance (UUID v4).",
            example="f47ac10b-58cc-4372-a567-0e02b2c3d479",
        ),
        EventFieldSchema(
            name="timestamp",
            type="string",
            description="ISO-8601 UTC timestamp of when the domain event was created.",
            example="2025-03-28T10:15:30.000000+00:00",
        ),
        EventFieldSchema(
            name="source",
            type="string",
            description="Service that produced the event.",
            example="workflow-control-plane",
        ),
    ]


# ---------------------------------------------------------------------------
# Use case
# ---------------------------------------------------------------------------

class GetSocketioConfigUseCase:
    """Return the complete Socket.IO event catalog for client integration."""

    def __init__(self, logger: Any=None, **kwargs: Any) -> None:
        self._logger = logger

    async def execute(self, command: GetSocketioConfigCommand) -> GetSocketioConfigResult:
        return GetSocketioConfigResult(
            events=_build_event_definitions(),
            rooms=_build_room_definitions(),
            meta_fields=_build_meta_fields(),
        )
