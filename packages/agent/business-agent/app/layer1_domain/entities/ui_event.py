from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List
from enum import Enum
from agent_sdk.layer1_domain.entities.agent_shared_state import TaskStatus
from app.layer1_domain.entities.next_step_suggestion import NextStepSuggestion


class UIPayloadType(str, Enum):
    PROGRESSING_COLLAPSE = "progressing_collapse"
    FORM_TOOL = "form_tool"
    TEXT = "text"
    BUTTON_GROUP = "button_group"
    OPEN_WORKSPACE = "open_workspace"
    VIEW_SPACE = "view_space"
    OPEN_VIEWER = "open_viewer"
    TOKEN_INFO = "token_info"


class UIPayloadText(str, Enum):
    OPEN_WORKSPACE = "Open Workspace"
    OPEN_VIEWER = "Open Viewer"


class UIEventType(str, Enum):
    CHAT_THINKING = "chat:thinking"
    CHAT_RESPONSE = "chat:response"
    CHAT_DISABLE = "chat:disable"
    CHAT_ENABLE = "chat:enable"
    PROCESS_RESPONSE = "process:response"
    

class UIEventRole(str, Enum):
    ASSISTANT = "assistant"
    USER = "user"


@dataclass
class UIActionReference:
    source_event_id: str
    source_conv_id: str


@dataclass
class UIMetadata:
    main_conv_id: str
    sub_conv_ids: List[str] = field(default_factory=list)
    uploaded_file_ids: List[str] = field(default_factory=list)
    action_context: Optional[UIActionReference] = None


@dataclass
class UIWFInfo:
    node_id: str
    task_id: str
    run_id: str


@dataclass
class UIPayload:
    id: str
    content: str

    parent_id: Optional[str] = None
    message_id: Optional[str] = None
    type: Optional[str] = None
    status: Optional[TaskStatus] = TaskStatus.PROCESSING
    sub_conv_id: Optional[str] = None
    text: Optional[List[str]] = None
    wf_info: Optional[UIWFInfo] = None
    object_type_id: Optional[str] = None
    record_id: Optional[str] = None
    change_request_id: Optional[str] = None
    next_step_suggestions: Optional[List[NextStepSuggestion]] = None


@dataclass
class UIEvent:
    event_id: str
    event_type: UIEventType
    correlation_id: str
    conv_id: str
    role: UIEventRole = UIEventRole.ASSISTANT

    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: Optional[UIMetadata] = None
    payload: Optional[UIPayload] = None


@dataclass
class UIActionEvent(UIEvent):
    wf_info: Optional[UIWFInfo] = None
    action: UIPayloadType = UIPayloadType.BUTTON_GROUP
    sub_conv_id: Optional[str] = None
    button_label: Optional[str] = None
    form_data: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UIActionEvent":
        return cls(**data)
    
