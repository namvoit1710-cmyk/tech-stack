from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from pydantic import BaseModel, Field

from agent_sdk.layer1_domain.entities.workflow_event import (
    EVENT_AGENT_SELECTED,
    EVENT_INPUT_UPDATED,
    EVENT_INPUT_VALIDATING,
    EVENT_NODE_COMPLETED,
    EVENT_NODE_DATA_INITIALIZED,
    EVENT_NODE_STARTED,
    EVENT_NODE_UPDATED,
    EVENT_NODE_WAITING_USER,
    EVENT_TOOL_SELECTED,
    EVENT_UI_RENDER_REQUEST,
    EVENT_UNSUPPORTED_FEATURE,
    EVENT_WORKFLOW_COMPLETED,
    EVENT_WORKFLOW_FAILED,
    EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST,
    EVENT_WORKFLOW_STARTED,
    WorkflowEvent,
)


class WorkflowEventPydantic(BaseModel):
    event_id: str
    event_type: str
    message: str = ""
    conv_id: str = ""
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = Field(default_factory=time.time)
    workflow_id: str = ""
    node_id: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_domain(cls, event: WorkflowEvent) -> "WorkflowEventPydantic":
        return cls(
            event_id=event.event_id,
            event_type=event.event_type,
            message=event.message,
            conv_id=event.conv_id,
            correlation_id=event.correlation_id,
            timestamp=event.timestamp,
            workflow_id=event.workflow_id,
            node_id=event.node_id,
            data=dict(event.data),
        )


class WorkflowStartedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_WORKFLOW_STARTED
    event_type: str = EVENT_WORKFLOW_STARTED
    message: str = "Workflow started"


class UiRenderRequestPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_UI_RENDER_REQUEST
    event_type: str = EVENT_UI_RENDER_REQUEST
    message: str = "The Workflow interface will be rendered in right side of chat."
    data: Dict[str, Any] = Field(default_factory=lambda: {"ui_yaml_blocks": "td/tr"})


class WorkflowGuidelineRenderRequestPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST
    event_type: str = EVENT_WORKFLOW_GUIDELINE_RENDER_REQUEST
    message: str = "{guideline}"


class NodeStartedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_NODE_STARTED
    event_type: str = EVENT_NODE_STARTED
    message: str = "Node execution started"


class InputValidatingPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_INPUT_VALIDATING
    event_type: str = EVENT_INPUT_VALIDATING
    message: str = "Validating input"


class NodeWaitingUserPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_NODE_WAITING_USER
    event_type: str = EVENT_NODE_WAITING_USER
    message: str = (
        "To processed, please update value for {label of missing_required_keys}"
    )
    data: Dict[str, Any] = Field(
        default_factory=lambda: {
            "missing_required_keys": [],
            "filled_required_keys": [],
        }
    )


class InputUpdatedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_INPUT_UPDATED
    event_type: str = EVENT_INPUT_UPDATED
    message: str = "Thanks for your update. To continue processed, please update value for {label of missing_required_keys}"
    data: Dict[str, Any] = Field(
        default_factory=lambda: {
            "missing_required_keys": [],
            "filled_required_keys": [],
        }
    )


class NodeCompletedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_NODE_COMPLETED
    event_type: str = EVENT_NODE_COMPLETED
    message: str = "Node {label} update successfully"


class WorkflowCompletedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_WORKFLOW_COMPLETED
    event_type: str = EVENT_WORKFLOW_COMPLETED
    message: str = "Workflow completed"


class WorkflowFailedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_WORKFLOW_FAILED
    event_type: str = EVENT_WORKFLOW_FAILED
    message: str = "Workflow failed"


class UnsupportedFeaturePydantic(WorkflowEventPydantic):
    event_id: str = EVENT_UNSUPPORTED_FEATURE
    event_type: str = EVENT_UNSUPPORTED_FEATURE
    message: str = "This feature is not supported in the current version."


class NodeUpdatedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_NODE_UPDATED
    event_type: str = EVENT_NODE_UPDATED
    message: str = "Node was updated."
    data: Dict[str, Any] = Field(
        default_factory=lambda: {"json_data": "{}", "file_id": ""}
    )


class NodeDataInitializedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_NODE_DATA_INITIALIZED
    event_type: str = EVENT_NODE_DATA_INITIALIZED
    message: str = "Update data for nodes."
    data: Dict[str, Any] = Field(default_factory=lambda: {"nodes": []})


class AgentSelectedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_AGENT_SELECTED
    event_type: str = EVENT_AGENT_SELECTED
    message: str = "Agent {name} selected"


class ToolSelectedPydantic(WorkflowEventPydantic):
    event_id: str = EVENT_TOOL_SELECTED
    event_type: str = EVENT_TOOL_SELECTED
    message: str = "Tool {name} selected"
