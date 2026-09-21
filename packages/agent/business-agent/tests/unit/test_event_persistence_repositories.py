from unittest.mock import MagicMock

import json

from app.layer1_domain.entities.ui_event import UIActionReference, UIEvent, UIEventRole, UIEventType, UIPayload, UIMetadata
from app.layer4_frameworks.repositories.hana_chat_timeline_node_repository import HanaChatTimelineNodeRepository
from app.layer4_frameworks.repositories.hana_ui_event_repository import HanaUIEventRepository


def test_hana_ui_event_repository_saves_role_and_payload():
    db = MagicMock()
    repo = HanaUIEventRepository(db)
    event = UIEvent(
        event_id="evt-1",
        event_type=UIEventType.CHAT_RESPONSE,
        correlation_id="corr-1",
        conv_id="conv-1",
        metadata=UIMetadata(main_conv_id="conv-1"),
        payload=UIPayload(id="payload-1", content="hello"),
    )

    repo.save_event(event)

    sql, params = db.execute_write.call_args.args
    assert sql.startswith("INSERT INTO AIW_AGENT_EVENT")
    assert params[1] == "assistant"
    assert params[2] == UIEventType.CHAT_RESPONSE.value


def test_hana_chat_timeline_node_repository_upserts_non_thinking_event():
    db = MagicMock()
    repo = HanaChatTimelineNodeRepository(db)
    event = UIEvent(
        event_id="evt-2",
        event_type=UIEventType.PROCESS_RESPONSE,
        correlation_id="corr-2",
        conv_id="conv-2",
        payload=UIPayload(id="payload-2", content="status"),
    )

    repo.upsert_from_event(event)

    sql, params = db.execute_write.call_args.args
    assert "MERGE INTO AIW_CHAT_TIMELINE_NODE" in sql
    assert params == ("conv-2", "payload-2", "evt-2")


def test_hana_chat_timeline_node_repository_skips_thinking_event():
    db = MagicMock()
    repo = HanaChatTimelineNodeRepository(db)
    event = UIEvent(
        event_id="evt-3",
        event_type=UIEventType.CHAT_THINKING,
        correlation_id="corr-3",
        conv_id="conv-3",
        payload=UIPayload(id="payload-3", content="thinking"),
    )

    repo.upsert_from_event(event)

    db.execute_write.assert_not_called()


def test_hana_chat_timeline_node_repository_skips_chat_toggle_event_without_payload():
    db = MagicMock()
    repo = HanaChatTimelineNodeRepository(db)
    event = UIEvent(
        event_id="evt-4",
        event_type=UIEventType.CHAT_DISABLE,
        correlation_id="corr-4",
        conv_id="conv-4",
    )

    repo.upsert_from_event(event)

    db.execute_write.assert_not_called()


def test_hana_ui_event_repository_serializes_action_reference_metadata():
    db = MagicMock()
    repo = HanaUIEventRepository(db)
    event = UIEvent(
        event_id="evt-4",
        event_type=UIEventType.PROCESS_RESPONSE,
        correlation_id="corr-4",
        conv_id="conv-1",
        metadata=UIMetadata(
            main_conv_id="conv-1",
            action_context=UIActionReference(
                source_event_id="source-evt-1",
                source_conv_id="conv-1",
            ),
        ),
        payload=UIPayload(id="payload-4", content="summary"),
    )

    repo.save_event(event)

    _, params = db.execute_write.call_args.args
    metadata = json.loads(params[6])
    assert metadata["action_context"] == {
        "source_event_id": "source-evt-1",
        "source_conv_id": "conv-1",
    }
