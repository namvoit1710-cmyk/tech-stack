from datetime import datetime, timezone
from types import SimpleNamespace

from app.layer1_domain.entities.orchestrator.ui_event import UIEventRole, UIEventType
from app.layer2_application.features.conversation.use_cases.get_conversation_messages_use_case import (
    GetConversationMessagesInput,
    GetConversationMessagesUseCase,
)
from app.layer2_application.interfaces.agent_event_repository import ReopenConversationRow


class DummyUuidBuilder:
    def create_uuid7(self) -> str:
        return "uuid-1"


def _row(*, event_id: str, event_type: UIEventType, role: UIEventRole, content: str) -> ReopenConversationRow:
    timestamp = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return ReopenConversationRow(
        conv_id="conv-1",
        payload_id=f"payload-{event_id}",
        event_id=event_id,
        event_type=event_type,
        correlation_id=event_id,
        node_created_at=timestamp,
        node_updated_at=timestamp,
        role=role,
        timestamp=timestamp,
        metadata={},
        payload={"id": f"payload-{event_id}", "content": content},
    )


def _make_subject(*, timeline_rows: list[ReopenConversationRow], last_toggle: ReopenConversationRow | None):
    repo = SimpleNamespace(
        get_reopen_conversation_rows=lambda conversation_id: timeline_rows,
        get_last_chat_toggle_event=lambda conversation_id: last_toggle,
        get_events_by_conversation_id=lambda conversation_id: [],
    )
    return GetConversationMessagesUseCase(
        logger=SimpleNamespace(),
        monitor=SimpleNamespace(),
        agent_event_repo=repo,
        uuid_builder=DummyUuidBuilder(),
    )


def test_get_conversation_messages_returns_timeline_when_no_toggle_events():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="evt-a", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
            _row(event_id="evt-b", event_type=UIEventType.CHAT_RESPONSE, role=UIEventRole.ASSISTANT, content="hi"),
        ],
        last_toggle=None,
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert [message.event_id for message in result.messages] == ["evt-a", "evt-b"]


def test_get_conversation_messages_returns_timeline_when_latest_toggle_is_enable():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="evt-a", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
            _row(event_id="evt-b", event_type=UIEventType.CHAT_RESPONSE, role=UIEventRole.ASSISTANT, content="hi"),
        ],
        last_toggle=_row(
            event_id="toggle-enable",
            event_type=UIEventType.CHAT_ENABLE,
            role=UIEventRole.ASSISTANT,
            content="",
        ),
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert [message.event_id for message in result.messages] == ["evt-a", "evt-b"]


def test_get_conversation_messages_prepends_latest_disable():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="evt-a", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
            _row(event_id="evt-b", event_type=UIEventType.CHAT_RESPONSE, role=UIEventRole.ASSISTANT, content="hi"),
        ],
        last_toggle=_row(
            event_id="toggle-disable",
            event_type=UIEventType.CHAT_DISABLE,
            role=UIEventRole.ASSISTANT,
            content="",
        ),
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert [message.event_id for message in result.messages] == ["toggle-disable", "evt-a", "evt-b"]


def test_get_conversation_messages_uses_latest_disable_after_previous_enable():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="evt-a", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
            _row(event_id="evt-b", event_type=UIEventType.CHAT_RESPONSE, role=UIEventRole.ASSISTANT, content="hi"),
        ],
        last_toggle=_row(
            event_id="toggle-disable-latest",
            event_type=UIEventType.CHAT_DISABLE,
            role=UIEventRole.ASSISTANT,
            content="",
        ),
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert result.messages[0].event_id == "toggle-disable-latest"


def test_get_conversation_messages_ignores_previous_disable_when_latest_toggle_is_enable():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="evt-a", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
            _row(event_id="evt-b", event_type=UIEventType.CHAT_RESPONSE, role=UIEventRole.ASSISTANT, content="hi"),
        ],
        last_toggle=_row(
            event_id="toggle-enable-latest",
            event_type=UIEventType.CHAT_ENABLE,
            role=UIEventRole.ASSISTANT,
            content="",
        ),
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert [message.event_id for message in result.messages] == ["evt-a", "evt-b"]


def test_get_conversation_messages_dedupes_latest_disable_already_in_timeline():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="toggle-disable", event_type=UIEventType.CHAT_DISABLE, role=UIEventRole.ASSISTANT, content=""),
            _row(event_id="evt-a", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
        ],
        last_toggle=_row(
            event_id="toggle-disable",
            event_type=UIEventType.CHAT_DISABLE,
            role=UIEventRole.ASSISTANT,
            content="",
        ),
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert [message.event_id for message in result.messages] == ["toggle-disable", "evt-a"]


def test_get_conversation_messages_preserves_order_after_prepending_disable():
    subject = _make_subject(
        timeline_rows=[
            _row(event_id="evt-b", event_type=UIEventType.MAIN_USER_CHAT, role=UIEventRole.USER, content="hello"),
            _row(event_id="evt-a", event_type=UIEventType.CHAT_RESPONSE, role=UIEventRole.ASSISTANT, content="hi"),
        ],
        last_toggle=_row(
            event_id="toggle-disable",
            event_type=UIEventType.CHAT_DISABLE,
            role=UIEventRole.ASSISTANT,
            content="",
        ),
    )

    result = subject.execute(GetConversationMessagesInput(conversation_id="conv-1"))

    assert [message.event_id for message in result.messages] == ["toggle-disable", "evt-b", "evt-a"]
