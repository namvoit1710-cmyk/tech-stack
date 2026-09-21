from dataclasses import dataclass, field
from datetime import datetime

from app.layer1_domain.entities.orchestrator.agent_event import AgentEvent
from app.layer1_domain.entities.orchestrator.ui_event import UIEvent, UIEventRole, UIEventType
from app.layer2_application.interfaces.agent_event_repository import ReopenConversationRow

from app.layer1_domain.interfaces.logger_interface import ILogger, IMonitor
from app.layer2_application.interfaces.agent_event_repository import IAgentEventRepository
from app.layer2_application.interfaces.uuid_builder_interface import IUuidBuilder


@dataclass
class GetConversationMessagesInput:
    conversation_id: str


@dataclass
class ReopenConversationEvent(UIEvent):
    id: str = ""
    content: str = ""
    created_at: str = ""
    timestamp: str = ""


@dataclass
class GetConversationMessagesOutput:
    messages: list[ReopenConversationEvent] = field(default_factory=list)
    total: int = 0


class GetConversationMessagesUseCase:
    def __init__(
        self,
        logger: ILogger,
        monitor: IMonitor,
        agent_event_repo: IAgentEventRepository,
        uuid_builder: IUuidBuilder,
    ):
        self.logger = logger
        self.monitor = monitor
        self.agent_event_repo = agent_event_repo
        self.uuid_builder = uuid_builder

    def execute(self, request: GetConversationMessagesInput) -> GetConversationMessagesOutput:
        rows = self.agent_event_repo.get_reopen_conversation_rows(request.conversation_id)
        last_toggle = self.agent_event_repo.get_last_chat_toggle_event(request.conversation_id)
        if not rows:
            events = self.agent_event_repo.get_events_by_conversation_id(request.conversation_id)
            messages = [self._to_message_from_event(event=event) for event in events]
        else:
            messages = [self._to_message_from_row(row=row) for row in rows]

        if self._is_chat_disable(last_toggle):
            messages = [self._to_message_from_row(row=last_toggle), *messages]

        messages = self._dedupe_by_event_id_preserving_order(messages)
        return GetConversationMessagesOutput(messages=messages, total=len(messages))

    @staticmethod
    def _dedupe_by_event_id_preserving_order(
        messages: list[ReopenConversationEvent],
    ) -> list[ReopenConversationEvent]:
        seen: set[str] = set()
        deduped_messages: list[ReopenConversationEvent] = []
        for message in messages:
            if message.event_id in seen:
                continue
            seen.add(message.event_id)
            deduped_messages.append(message)
        return deduped_messages

    @classmethod
    def _is_chat_disable(cls, row: ReopenConversationRow | None) -> bool:
        if row is None:
            return False
        return cls._event_type_value(row.event_type) == UIEventType.CHAT_DISABLE.value

    @staticmethod
    def _event_type_value(event_type: UIEventType | str | None) -> str:
        if isinstance(event_type, UIEventType):
            return event_type.value
        if isinstance(event_type, str):
            return event_type
        return ""

    def _to_message_from_row(self, *, row: ReopenConversationRow) -> ReopenConversationEvent:
        message_id = self.uuid_builder.create_uuid7()
        return ReopenConversationEvent(
            id=message_id,
            content=self._payload_content(row.payload),
            created_at=self._to_iso(row.node_created_at),
            event_id=row.event_id,
            role=row.role,
            event_type=row.event_type,
            timestamp=self._to_iso(row.timestamp),
            correlation_id=row.correlation_id,
            conv_id=row.conv_id,
            metadata=row.metadata,
            payload=row.payload,
        )

    def _to_message_from_event(self, *, event: AgentEvent) -> ReopenConversationEvent:
        payload = event.payload or {}
        message_id = self.uuid_builder.create_uuid7()
        return ReopenConversationEvent(
            id=message_id,
            content=self._payload_content(payload),
            created_at=self._to_iso(event.timestamp),
            event_id=event.event_id,
            role=event.role,
            event_type=event.event_type,
            timestamp=self._to_iso(event.timestamp),
            correlation_id=event.correlation_id,
            conv_id=event.conv_id,
            metadata=event.metadata or {},
            payload=payload,
        )

    @staticmethod
    def _payload_content(payload: dict) -> str:
        content = payload.get("content", "")
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _to_iso(value) -> str:
        if value == datetime.min:
            return ""
        return value.isoformat() if hasattr(value, "isoformat") else str(value or "")
