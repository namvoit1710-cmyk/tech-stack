import json
from unittest.mock import MagicMock

from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
    BaseHanaRepository,
)

from app.layer4_frameworks.repositories.hana_business_agent_shared_state_repository import (
    HanaBusinessAgentSharedStateRepository,
)

_STATES_TABLE = '"AIW_AGENT_STATES"'


def _make_db() -> MagicMock:
    return MagicMock()


def test_get_conversation_id_by_run_id_returns_matching_conversation():
    db = _make_db()
    db.execute_query.return_value = [
        {
            "CONV_ID": "conv-1",
            "METADATA": json.dumps({"current_run_id": "run-1"}),
        },
        {
            "CONV_ID": "conv-2",
            "METADATA": json.dumps({"current_run_id": "run-2"}),
        },
    ]
    subject = HanaBusinessAgentSharedStateRepository(
        BaseHanaRepository(db, _STATES_TABLE)
    )

    result = subject.get_conversation_id_by_run_id("run-2")

    assert result == "conv-2"
    sql = db.execute_query.call_args.args[0]
    assert 'SELECT "CONV_ID", "METADATA"' in sql
    assert 'FROM "AIW_AGENT_STATES"' in sql


def test_get_conversation_id_by_run_id_returns_none_when_missing():
    db = _make_db()
    db.execute_query.return_value = [
        {
            "conv_id": "conv-1",
            "metadata": json.dumps({"current_run_id": "run-1"}),
        }
    ]
    subject = HanaBusinessAgentSharedStateRepository(
        BaseHanaRepository(db, _STATES_TABLE)
    )

    result = subject.get_conversation_id_by_run_id("missing")

    assert result is None


def _states_row_with_message_executions() -> dict:
    context = {
        "conversation_context": [
            {
                "message_id": "msg-0",
                "message": "old",
                "tasks": [{"task_id": "task-3", "knowledge": []}],
            },
            {
                "message_id": "msg-1",
                "message": "current",
                "tasks": [
                    {"task_id": "task-1", "knowledge": []},
                    {"task_id": "task-2", "knowledge": []},
                ],
            },
        ]
    }
    executions = [
        {"task_id": "task-1", "agent": "tool.a", "name": "A", "status": "success"},
        {"task_id": "task-2", "agent": "tool.b", "name": "B", "status": "success"},
        {"task_id": "task-3", "agent": "tool.c", "name": "C", "status": "success"},
    ]
    return {"CONTEXT": json.dumps(context), "EXECUTIONS": json.dumps(executions)}


def test_get_executions_by_message_id_filters_to_message_tasks():
    db = _make_db()
    db.execute_query.return_value = [_states_row_with_message_executions()]
    subject = HanaBusinessAgentSharedStateRepository(
        BaseHanaRepository(db, _STATES_TABLE)
    )

    result = subject.get_executions_by_message_id("conv-1", "msg-1")

    # task-3 belongs to msg-0 and must be excluded
    assert [task.id for task in result] == ["task-1", "task-2"]
    sql = db.execute_query.call_args.args[0]
    assert 'SELECT "CONTEXT", "EXECUTIONS"' in sql
    assert 'WHERE "CONV_ID" = :p0' in sql


def test_get_executions_by_message_id_returns_empty_when_message_missing():
    db = _make_db()
    db.execute_query.return_value = [_states_row_with_message_executions()]
    subject = HanaBusinessAgentSharedStateRepository(
        BaseHanaRepository(db, _STATES_TABLE)
    )

    assert subject.get_executions_by_message_id("conv-1", "missing") == []


def test_get_executions_by_message_id_returns_empty_when_conversation_missing():
    db = _make_db()
    db.execute_query.return_value = []
    subject = HanaBusinessAgentSharedStateRepository(
        BaseHanaRepository(db, _STATES_TABLE)
    )

    assert subject.get_executions_by_message_id("missing", "msg-1") == []