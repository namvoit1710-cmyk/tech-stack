from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict

from agent_sdk.layer1_domain.entities.workflow_event import (
    AgentSelectedEvent,
    InputUpdatedEvent,
    InputValidatingEvent,
    NodeCompletedEvent,
    NodeDataInitializedEvent,
    NodeStartedEvent,
    NodeUpdatedEvent,
    NodeWaitingUserEvent,
    ToolSelectedEvent,
    UiRenderRequestEvent,
    UnsupportedFeatureEvent,
    WorkflowCompletedEvent,
    WorkflowEvent,
    WorkflowFailedEvent,
    WorkflowGuidelineRenderRequestEvent,
    WorkflowStartedEvent,
)


@dataclass
class EventDto:
    event_id: str
    event_type: str
    message: str = ""
    conv_id: str = ""
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    workflow_id: str = ""
    node_id: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_domain(cls, event: WorkflowEvent) -> "EventDto":
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


WorkflowStartedEventDto = WorkflowStartedEvent
UiRenderRequestEventDto = UiRenderRequestEvent
WorkflowGuidelineRenderRequestEventDto = WorkflowGuidelineRenderRequestEvent
NodeStartedEventDto = NodeStartedEvent
InputValidatingEventDto = InputValidatingEvent
NodeWaitingUserEventDto = NodeWaitingUserEvent
InputUpdatedEventDto = InputUpdatedEvent
NodeCompletedEventDto = NodeCompletedEvent
WorkflowCompletedEventDto = WorkflowCompletedEvent
WorkflowFailedEventDto = WorkflowFailedEvent
UnsupportedFeatureEventDto = UnsupportedFeatureEvent
NodeUpdatedEventDto = NodeUpdatedEvent
NodeDataInitializedEventDto = NodeDataInitializedEvent
AgentSelectedEventDto = AgentSelectedEvent
ToolSelectedEventDto = ToolSelectedEvent
