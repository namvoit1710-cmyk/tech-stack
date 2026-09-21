from dataclasses import dataclass
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.layer2_application.interfaces.conversation_summary_repository import ConversationTitleSource, ConversationTitleSummary
from app.layer1_domain.entities.orchestrator.ui_event import UIEvent, UIEventRole, UIEventType, UIPayload
from app.layer4_frameworks.repositories.agent_event_repository import HanaAgentEventRepository
from app.layer4_frameworks.repositories.conversation_summary_repository import HanaConversationSummaryRepository


@dataclass
class DemoData:
	name: str
	tags: set[str]


def test_agent_event_repository_serializes_metadata_and_payload():
	db = MagicMock()
	repo = HanaAgentEventRepository(db)
	event = UIEvent(
		event_id="evt-1",
		role=UIEventRole.USER,
		event_type=UIEventType.CHAT_RESPONSE,
		correlation_id="corr-1",
		conv_id="conv-1",
	)

	repo.save_event(event)

	sql, params = db.execute_write.call_args.args
	assert sql.startswith("INSERT INTO AIW_AGENT_EVENT")
	assert params[0] == "evt-1"
	assert params[1] == "user"
	assert params[2] == UIEventType.CHAT_RESPONSE.value
	assert params[6] == "null"
	assert params[7] == "null"


def test_agent_event_repository_get_events_by_conversation_id_orders_results():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"EVENT_ID": "evt-1",
			"ROLE": "user",
			"EVENT_TYPE": UIEventType.MAIN_USER_CHAT.value,
			"TIMESTAMP": "2024-01-01T00:00:00+00:00",
			"CORRELATION_ID": "corr-1",
			"CONV_ID": "conv-1",
			"METADATA": '{"main_conv_id": "conv-1"}',
			"PAYLOAD": '{"id": "payload-1", "content": "hello"}',
		},
	]
	repo = HanaAgentEventRepository(db)

	result = repo.get_events_by_conversation_id("conv-1")

	sql, params = db.execute_query.call_args.args
	assert "WHERE CONV_ID = :p0" in sql
	assert "ORDER BY TIMESTAMP ASC" in sql
	assert params == ("conv-1",)
	assert result[0].event_id == "evt-1"
	assert result[0].role == UIEventRole.USER
	assert result[0].metadata is not None
	assert result[0].metadata["main_conv_id"] == "conv-1"
	assert result[0].payload["content"] == "hello"


def test_agent_event_repository_lists_latest_conversation_events():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"event_id": "evt-2",
			"role": "assistant",
			"event_type": UIEventType.CHAT_RESPONSE.value,
			"timestamp": datetime(2024, 1, 2, tzinfo=timezone.utc),
			"correlation_id": "corr-2",
			"conv_id": "conv-2",
			"metadata": None,
			"payload": '{"id": "payload-2", "content": "world", "wf_info": {"node_id": "n1", "task_id": "t1", "run_id": "r1"}}',
		},
	]
	repo = HanaAgentEventRepository(db)

	result = repo.list_latest_conversation_events(skip=5, top=3)

	sql, params = db.execute_query.call_args.args
	assert "GROUP BY CONV_ID" in sql
	assert "LIMIT :p0 OFFSET :p1" in sql
	assert params == (3, 5)
	assert result[0].conv_id == "conv-2"
	assert result[0].role == UIEventRole.ASSISTANT
	assert result[0].payload["wf_info"]["run_id"] == "r1"


def test_agent_event_repository_maps_unknown_event_type_without_crashing():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"EVENT_ID": "evt-3",
			"ROLE": "assistant",
			"EVENT_TYPE": "conversation.plan.created",
			"TIMESTAMP": "2024-01-03T00:00:00+00:00",
			"CORRELATION_ID": "corr-3",
			"CONV_ID": "conv-3",
			"METADATA": None,
			"PAYLOAD": '{"id": "payload-3", "content": "planned"}',
		},
	]
	repo = HanaAgentEventRepository(db)

	result = repo.get_events_by_conversation_id("conv-3")

	assert result[0].event_type == "conversation.plan.created"
	assert result[0].payload["content"] == "planned"


def test_agent_event_repository_upserts_timeline_event():
	db = MagicMock()
	repo = HanaAgentEventRepository(db)
	event = UIEvent(
		event_id="evt-4",
		role=UIEventRole.USER,
		event_type=UIEventType.MAIN_USER_CHAT,
		correlation_id="corr-4",
		conv_id="conv-4",
		payload=UIPayload(id="payload-4", content="hello"),
	)

	repo.upsert_timeline_event(event)

	sql, params = db.execute_write.call_args.args
	assert "MERGE INTO AIW_CHAT_TIMELINE_NODE" in sql
	assert params == ("conv-4", "payload-4", "evt-4")


def test_agent_event_repository_reads_reopen_rows():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"CONV_ID": "conv-5",
			"PAYLOAD_ID": "payload-5",
			"NODE_CREATED_AT": datetime(2024, 1, 5, tzinfo=timezone.utc),
			"NODE_UPDATED_AT": datetime(2024, 1, 5, tzinfo=timezone.utc),
			"EVENT_ID": "evt-5",
			"ROLE": "assistant",
			"EVENT_TYPE": UIEventType.PROCESS_RESPONSE.value,
			"TIMESTAMP": datetime(2024, 1, 5, tzinfo=timezone.utc),
			"CORRELATION_ID": "corr-5",
			"METADATA": '{"main_conv_id": "conv-5"}',
			"PAYLOAD": '{"id": "payload-5", "content": "world"}',
		},
	]
	repo = HanaAgentEventRepository(db)

	result = repo.get_reopen_conversation_rows("conv-5")

	sql, params = db.execute_query.call_args.args
	assert "FROM AIW_CHAT_TIMELINE_NODE AS timeline_row" in sql
	assert "ORDER BY timeline_row.CREATED_AT ASC, timeline_row.PAYLOAD_ID ASC" in sql
	assert params == ("conv-5",)
	assert result[0].role == UIEventRole.ASSISTANT
	assert result[0].payload["content"] == "world"


def test_agent_event_repository_reads_last_chat_toggle_event():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"CONV_ID": "conv-5",
			"PAYLOAD_ID": "evt-toggle",
			"NODE_CREATED_AT": datetime(2024, 1, 5, tzinfo=timezone.utc),
			"NODE_UPDATED_AT": datetime(2024, 1, 5, tzinfo=timezone.utc),
			"EVENT_ID": "evt-toggle",
			"ROLE": "assistant",
			"EVENT_TYPE": UIEventType.CHAT_DISABLE.value,
			"TIMESTAMP": datetime(2024, 1, 5, tzinfo=timezone.utc),
			"CORRELATION_ID": "corr-5",
			"METADATA": '{"main_conv_id": "conv-5"}',
			"PAYLOAD": '{}',
		},
	]
	repo = HanaAgentEventRepository(db)

	result = repo.get_last_chat_toggle_event("conv-5")

	sql, params = db.execute_query.call_args.args
	assert "FROM AIW_AGENT_EVENT AS event_row" in sql
	assert "EVENT_TYPE IN ('chat:disable', 'chat:enable')" in sql
	assert "ORDER BY event_row.TIMESTAMP DESC, event_row.EVENT_ID DESC" in sql
	assert "LIMIT 1" in sql
	assert params == ("conv-5",)
	assert result is not None
	assert result.event_id == "evt-toggle"
	assert result.event_type == UIEventType.CHAT_DISABLE


def test_agent_event_repository_returns_none_when_last_chat_toggle_missing():
	db = MagicMock()
	db.execute_query.return_value = []
	repo = HanaAgentEventRepository(db)

	result = repo.get_last_chat_toggle_event("conv-5")

	assert result is None


def test_agent_event_repository_get_event_by_id_returns_row_when_found():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"EVENT_ID": "evt-6",
			"ROLE": "assistant",
			"EVENT_TYPE": UIEventType.PROCESS_RESPONSE.value,
			"TIMESTAMP": "2024-01-06T00:00:00+00:00",
			"CORRELATION_ID": "corr-6",
			"CONV_ID": "conv-6",
			"METADATA": '{"main_conv_id": "conv-6"}',
			"PAYLOAD": '{"id": "payload-6", "content": "ok"}',
		},
	]
	repo = HanaAgentEventRepository(db)

	result = repo.get_event_by_id("evt-6")

	sql, params = db.execute_query.call_args.args
	assert "WHERE EVENT_ID = :p0" in sql
	assert params == ("evt-6",)
	assert result is not None
	assert result.event_id == "evt-6"


def test_agent_event_repository_get_event_by_id_returns_none_when_missing():
	db = MagicMock()
	db.execute_query.return_value = []
	repo = HanaAgentEventRepository(db)

	result = repo.get_event_by_id("missing")

	assert result is None


def test_conversation_summary_repository_saves_title_with_merge():
	db = MagicMock()
	repo = HanaConversationSummaryRepository(db)

	repo.save_title(
		ConversationTitleSummary(
			conv_id="conv-1",
			title="Plan invoice approval",
			source=ConversationTitleSource.STRATEGY,
		)
	)

	sql, params = db.execute_write.call_args.args
	assert "MERGE INTO AIW_CONVERSATION_SUMMARY" in sql
	assert params == ("conv-1", "Plan invoice approval", ConversationTitleSource.STRATEGY.value)


def test_conversation_summary_repository_reads_single_title():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"CONV_ID": "conv-1",
			"TITLE": "Plan invoice approval",
			"TITLE_SOURCE": ConversationTitleSource.STRATEGY.value,
		},
	]
	repo = HanaConversationSummaryRepository(db)

	result = repo.get_title("conv-1")

	assert result is not None
	assert result.conv_id == "conv-1"
	assert result.title == "Plan invoice approval"
	assert result.source == ConversationTitleSource.STRATEGY


def test_conversation_summary_repository_reads_titles_in_bulk():
	db = MagicMock()
	db.execute_query.return_value = [
		{
			"CONV_ID": "conv-1",
			"TITLE": "Plan invoice approval",
			"TITLE_SOURCE": ConversationTitleSource.STRATEGY.value,
		},
		{
			"CONV_ID": "conv-2",
			"TITLE": "Fallback title",
			"TITLE_SOURCE": ConversationTitleSource.USER_MESSAGE.value,
		},
	]
	repo = HanaConversationSummaryRepository(db)

	result = repo.get_titles(["conv-1", "conv-2"])

	sql, params = db.execute_query.call_args.args
	assert "WHERE CONV_ID IN (:p0, :p1)" in sql
	assert params == ("conv-1", "conv-2")
	assert result["conv-1"].source == ConversationTitleSource.STRATEGY
	assert result["conv-2"].title == "Fallback title"
