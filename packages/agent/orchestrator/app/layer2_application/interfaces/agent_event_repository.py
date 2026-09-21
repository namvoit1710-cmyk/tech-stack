"""Agent event repository port."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer1_domain.entities.orchestrator.ui_event import UIEventRole, UIEvent, UIEventType


@dataclass(slots=True)
class ReopenConversationRow:
    conv_id: str
    payload_id: str
    event_id: str
    event_type: UIEventType
    correlation_id: str
    node_created_at: datetime = field(default_factory=lambda: datetime.min)
    node_updated_at: datetime = field(default_factory=lambda: datetime.min)
    role: UIEventRole = UIEventRole.ASSISTANT
    timestamp: datetime = field(default_factory=lambda: datetime.min)
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


class IAgentEventRepository(Protocol):
    def save_event(self, event: UIEvent) -> None: ...
    def upsert_timeline_event(self, event: UIEvent) -> None: ...
    def get_event_by_id(self, event_id: str) -> AgentEvent | None: ...
    def get_events_by_conversation_id(self, conversation_id: str) -> list[AgentEvent]: ...
    def list_latest_conversation_events(self, skip: int = 0, top: int = 10) -> list[AgentEvent]: ...

    def get_reopen_conversation_rows(self, conversation_id: str) -> list[ReopenConversationRow]: ...
    def get_last_chat_toggle_event(self, conversation_id: str) -> ReopenConversationRow | None: ...
