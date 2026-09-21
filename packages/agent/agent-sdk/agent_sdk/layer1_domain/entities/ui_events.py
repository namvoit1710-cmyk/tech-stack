from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class UiEventType(str, Enum):
    CHAT_THINKING = "chat:thinking"
    CHAT_RESPONSE = "chat:response"
    CHAT_DISABLED = "chat:disabled"
    CHAT_ENABLED = "chat:enabled"


class UiPayloadType(str, Enum):
    PROGRESSING_COLLAPSE = "progressing_collapse"
    TOOL_FORM = "tool_form"
    TEXT = "text"
    BUTTON_GROUP = "button_group"
    OPEN_WORKSPACE = "open_workspace"
    SUMMARY = "summary"


@dataclass
class WfInfo:
    node_id: str = ""
    task_id: str = ""
    run_id: str = ""


@dataclass
class BaseUiPayload:
    id: str = ""
    parent_id: str | None = None
    type: str = ""
    content: str = ""
    status: str = "processing"


@dataclass
class ProgressingCollapsePayload(BaseUiPayload):
    type: str = UiPayloadType.PROGRESSING_COLLAPSE.value
    title: str = ""
    message: str = ""

    def __post_init__(self) -> None:
        if not self.content:
            self.content = self.message or self.title


@dataclass
class ToolFormPayload(BaseUiPayload):
    form_schema: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    type: str = UiPayloadType.TOOL_FORM.value
    wf_info: WfInfo | None = None
    tool_name: str = ""

    def __post_init__(self) -> None:
        if not self.content:
            self.content = self.tool_name


@dataclass
class TextPayload(BaseUiPayload):
    type: str = UiPayloadType.TEXT.value
    text: str = ""

    def __post_init__(self) -> None:
        if not self.content:
            self.content = self.text
        elif not self.text:
            self.text = self.content


@dataclass
class ButtonAction:
    label: str
    value: str
    disabled: bool = False


@dataclass
class ButtonGroupPayload(BaseUiPayload):
    type: str = UiPayloadType.BUTTON_GROUP.value
    text: list[str] = field(default_factory=list)
    buttons: list[ButtonAction] = field(default_factory=list)
    title: str = ""

    def __post_init__(self) -> None:
        if not self.text and self.buttons:
            self.text = [button.label for button in self.buttons]
        if not self.content:
            self.content = self.title


@dataclass
class OpenWorkspacePayload(BaseUiPayload):
    workspace_id: str = ""
    node_id: str = ""
    task_id: str = ""
    run_id: str = ""
    file_id: str = ""
    type: str = UiPayloadType.OPEN_WORKSPACE.value


@dataclass
class SummaryPayload(BaseUiPayload):
    type: str = UiPayloadType.SUMMARY.value


@dataclass
class BaseUiEvent:
    event_id: str = ""
    event_type: str = ""
    correlation_id: str = ""
    timestamp: str | float | int | None = None
    conv_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: Any = field(default_factory=dict)
    conversation_id: str = ""

    def __post_init__(self) -> None:
        if self.conversation_id and not self.conv_id:
            self.conv_id = self.conversation_id
        elif self.conv_id and not self.conversation_id:
            self.conversation_id = self.conv_id

    @property
    def type(self) -> str:
        return self.event_type

    @type.setter
    def type(self, value: str) -> None:
        self.event_type = value


@dataclass
class ChatThinkingEvent(BaseUiEvent):
    event_type: str = UiEventType.CHAT_THINKING.value
    payload: Any = field(default_factory=dict)


@dataclass
class ChatResponseEvent(BaseUiEvent):
    event_type: str = UiEventType.CHAT_RESPONSE.value


@dataclass
class ChatDisabledEvent(BaseUiEvent):
    event_type: str = UiEventType.CHAT_DISABLED.value
    payload: Any = field(default_factory=dict)


@dataclass
class ChatEnabledEvent(BaseUiEvent):
    event_type: str = UiEventType.CHAT_ENABLED.value
    payload: Any = field(default_factory=dict)
