from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, List
from enum import Enum

class UIPayloadType(str, Enum):
    PROGRESSING_COLLAPSE = "progressing_collapse"
    FORM_TOOL = "form_tool"
    TEXT = "text"
    BUTTON_GROUP = "button_group"
    OPEN_WORKSPACE = "open_workspace"
    MAIN_USER_CHAT = "main_user_chat"
    SUB_USER_CHAT = "sub_user_chat"
    TOKEN_INFO = "token_info"

class UIEventType(str, Enum):
    CHAT_THINKING = "chat:thinking"
    CHAT_RESPONSE = "chat:response"
    CHAT_DISABLE = "chat:disable"
    CHAT_ENABLE = "chat:enable"
    PROCESS_RESPONSE = "process:response"
    MAIN_USER_CHAT = "main:user:chat"
    SUB_USER_CHAT = "sub:user:chat"
    USER_ACTION = "user:action"
    
class UIEventStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class UIEventRole(str, Enum):
    ASSISTANT = "assistant"
    USER = "user"


@dataclass(slots=True)
class UIActionReference:
    source_event_id: str
    source_conv_id: str

@dataclass(slots=True)
class UIMetadata():
    main_conv_id: str
    sub_conv_ids: List[str] = field(default_factory=list)
    uploaded_file_ids: List[str] = field(default_factory=list)
    action_context: Optional[UIActionReference] = None


@dataclass(slots=True)
class UIWFInfo():
    node_id: str
    task_id: str
    run_id: str


@dataclass(slots=True)
class UIPayload():
    id: str
    parent_id: Optional[str] = None
    message_id: Optional[str] = None
    type: Optional[UIPayloadType] = None
    content: str = ""
    status: Optional[str] = None
    sub_conv_id: Optional[str] = None
    text: Optional[List[str]] = None
    wf_info: Optional[UIWFInfo] = None


@dataclass(slots=True)
class UIEvent():
    event_id: str = ""
    role: UIEventRole = UIEventRole.ASSISTANT
    event_type: UIEventType = UIEventType.MAIN_USER_CHAT
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""
    conv_id: str = ""
    metadata: Optional[UIMetadata] = None
    payload: Optional[UIPayload] = None
