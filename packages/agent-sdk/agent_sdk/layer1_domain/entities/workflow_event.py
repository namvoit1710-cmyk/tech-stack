from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

EVENT_WORKFLOW_STARTED = "WORKFLOW_STARTED"
EVENT_UI_RENDER_REQUEST = "UI_RENDER_REQUEST"
EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST = "WORKFLOW_GUIDELINE_RENDER_REQUEST"
EVENT_NODE_STARTED = "NODE_STARTED"
EVENT_INPUT_VALIDATING = "INPUT_VALIDATING"
EVENT_NODE_WAITING_USER = "NODE_WAITING_USER"
EVENT_INPUT_UPDATED = "INPUT_UPDATED"
EVENT_NODE_COMPLETED = "NODE_COMPLETED"
EVENT_WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
EVENT_WORKFLOW_FAILED = "WORKFLOW_FAILED"
EVENT_UNSUPPORTED_FEATURE = "UNSUPPORTED_FEATURE"
EVENT_NODE_UPDATED = "NODE_UPDATED"
EVENT_NODE_DATA_INITIALIZED = "NODE_DATA_INITIALIZED"
EVENT_AGENT_SELECTED = "AGENT_SELECTED"
EVENT_TOOL_SELECTED = "TOOL_SELECTED"

__all__ = [
    "EVENT_WORKFLOW_STARTED",
    "EVENT_UI_RENDER_REQUEST",
    "EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST",
    "EVENT_NODE_STARTED",
    "EVENT_INPUT_VALIDATING",
    "EVENT_NODE_WAITING_USER",
    "EVENT_INPUT_UPDATED",
    "EVENT_NODE_COMPLETED",
    "EVENT_WORKFLOW_COMPLETED",
    "EVENT_WORKFLOW_FAILED",
    "EVENT_UNSUPPORTED_FEATURE",
    "EVENT_NODE_UPDATED",
    "EVENT_NODE_DATA_INITIALIZED",
    "EVENT_AGENT_SELECTED",
    "EVENT_TOOL_SELECTED",
    "WorkflowEvent",
    "WorkflowStartedEvent",
    "UiRenderRequestEvent",
    "WorkflowGuidelineRenderRequestEvent",
    "NodeStartedEvent",
    "InputValidatingEvent",
    "NodeWaitingUserEvent",
    "InputUpdatedEvent",
    "NodeCompletedEvent",
    "WorkflowCompletedEvent",
    "WorkflowFailedEvent",
    "UnsupportedFeatureEvent",
    "NodeUpdatedEvent",
    "NodeDataInitializedEvent",
    "AgentSelectedEvent",
    "ToolSelectedEvent",
]


@dataclass
class WorkflowEvent:
    event_id: str
    event_type: str
    message: str = ""
    conv_id: str = ""
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    workflow_id: str = ""
    node_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStartedEvent(WorkflowEvent):
    event_id: str = EVENT_WORKFLOW_STARTED
    event_type: str = EVENT_WORKFLOW_STARTED
    message: str = "Workflow started"


@dataclass
class UiRenderRequestEvent(WorkflowEvent):
    event_id: str = EVENT_UI_RENDER_REQUEST
    event_type: str = EVENT_UI_RENDER_REQUEST
    message: str = "The Workflow interface will be rendered in right side of chat."
    data: Dict[str, Any] = field(default_factory=lambda: {"ui_yaml_blocks": "td/tr"})


@dataclass
class WorkflowGuidelineRenderRequestEvent(WorkflowEvent):
    event_id: str = EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST
    event_type: str = EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST
    message: str = "{guideline}"


@dataclass
class NodeStartedEvent(WorkflowEvent):
    event_id: str = EVENT_NODE_STARTED
    event_type: str = EVENT_NODE_STARTED
    message: str = "Node execution started"


@dataclass
class InputValidatingEvent(WorkflowEvent):
    event_id: str = EVENT_INPUT_VALIDATING
    event_type: str = EVENT_INPUT_VALIDATING
    message: str = "Validating input"


@dataclass
class NodeWaitingUserEvent(WorkflowEvent):
    event_id: str = EVENT_NODE_WAITING_USER
    event_type: str = EVENT_NODE_WAITING_USER
    message: str = (
        "To processed, please update value for {label of missing_required_keys}"
    )
    data: Dict[str, Any] = field(
        default_factory=lambda: {
            "missing_required_keys": [],
            "filled_required_keys": [],
        }
    )


@dataclass
class InputUpdatedEvent(WorkflowEvent):
    event_id: str = EVENT_INPUT_UPDATED
    event_type: str = EVENT_INPUT_UPDATED
    message: str = "Thanks for your update. To continue processed, please update value for {label of missing_required_keys}"
    data: Dict[str, Any] = field(
        default_factory=lambda: {
            "missing_required_keys": [],
            "filled_required_keys": [],
        }
    )


@dataclass
class NodeCompletedEvent(WorkflowEvent):
    event_id: str = EVENT_NODE_COMPLETED
    event_type: str = EVENT_NODE_COMPLETED
    message: str = "Node {label} update successfully"


@dataclass
class WorkflowCompletedEvent(WorkflowEvent):
    event_id: str = EVENT_WORKFLOW_COMPLETED
    event_type: str = EVENT_WORKFLOW_COMPLETED
    message: str = "Workflow completed"


@dataclass
class WorkflowFailedEvent(WorkflowEvent):
    event_id: str = EVENT_WORKFLOW_FAILED
    event_type: str = EVENT_WORKFLOW_FAILED
    message: str = "Workflow failed"


@dataclass
class UnsupportedFeatureEvent(WorkflowEvent):
    event_id: str = EVENT_UNSUPPORTED_FEATURE
    event_type: str = EVENT_UNSUPPORTED_FEATURE
    message: str = "This feature is not supported in the current version."


@dataclass
class NodeUpdatedEvent(WorkflowEvent):
    event_id: str = EVENT_NODE_UPDATED
    event_type: str = EVENT_NODE_UPDATED
    message: str = "Node was updated."
    data: Dict[str, Any] = field(
        default_factory=lambda: {"json_data": "{}", "file_id": ""}
    )


@dataclass
class NodeDataInitializedEvent(WorkflowEvent):
    event_id: str = EVENT_NODE_DATA_INITIALIZED
    event_type: str = EVENT_NODE_DATA_INITIALIZED
    message: str = "Update data for nodes."
    data: Dict[str, Any] = field(default_factory=lambda: {"nodes": []})


@dataclass
class AgentSelectedEvent(WorkflowEvent):
    event_id: str = EVENT_AGENT_SELECTED
    event_type: str = EVENT_AGENT_SELECTED
    message: str = "Agent {name} selected"


@dataclass
class ToolSelectedEvent(WorkflowEvent):
    event_id: str = EVENT_TOOL_SELECTED
    event_type: str = EVENT_TOOL_SELECTED
    message: str = "Tool {name} selected"
