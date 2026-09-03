import json
from io import StringIO
from unittest.mock import MagicMock

from agent_sdk.layer1_domain.entities.inbox_record import InboxRecord
from agent_sdk.layer4_frameworks.persistence.hana.hana_inbox_repository import (
    HanaInboxRepository,
)


def _make_repo() -> tuple[HanaInboxRepository, MagicMock]:
    db = MagicMock()
    repo = HanaInboxRepository(db=db)
    return repo, db


class TestHanaInboxRepositorySetup:
    def test_setup_creates_table(self):
        repo, db = _make_repo()
        conn = MagicMock()
        cursor = MagicMock()
        db._create_connection.return_value = conn
        conn.cursor.return_value = cursor
        repo.setup()
        cursor.execute.assert_called_once()
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_setup_ignores_table_exists_error(self):
        import hdbcli.dbapi

        repo, db = _make_repo()
        conn = MagicMock()
        cursor = MagicMock()
        db._create_connection.return_value = conn
        conn.cursor.return_value = cursor
        err = hdbcli.dbapi.Error()
        err.errorcode = 288
        cursor.execute.side_effect = err
        repo.setup()
        conn.close.assert_called_once()


class TestHanaInboxRepositoryFindByMessageId:
    def test_returns_none_when_not_found(self):
        repo, db = _make_repo()
        db.execute_query.return_value = []
        result = repo.find_by_message_id("msg-1")
        assert result is None

    def test_returns_record_when_found(self):
        repo, db = _make_repo()
        db.execute_query.return_value = [
            {
                "message_id": "msg-1",
                "idempotency_key": "idem-1",
                "payload": '{"type": "test"}',
                "status": "RECEIVED",
                "received_at": "2026-01-01T00:00:00+00:00",
                "processed_at": "",
                "error": "",
            }
        ]
        result = repo.find_by_message_id("msg-1")
        assert result is not None
        assert result.message_id == "msg-1"
        assert result.payload == {"type": "test"}
        assert result.status == "RECEIVED"

    def test_handles_lob_payload(self):
        repo, db = _make_repo()
        db.execute_query.return_value = [
            {
                "message_id": "msg-1",
                "idempotency_key": "idem-1",
                "payload": StringIO('{"key": "val"}'),
                "status": "COMPLETED",
                "received_at": "t",
                "processed_at": "t2",
                "error": StringIO("some error"),
            }
        ]
        result = repo.find_by_message_id("msg-1")
        assert result is not None
        assert result.payload == {"key": "val"}
        assert result.error == "some error"


class TestHanaInboxRepositorySave:
    def test_save_calls_merge(self):
        repo, db = _make_repo()
        record = InboxRecord(
            message_id="msg-1",
            idempotency_key="idem-1",
            payload={"type": "test"},
            status="RECEIVED",
            received_at="t",
        )
        result = repo.save(record)
        assert result == record
        db.execute_write.assert_called_once()
        call_args = db.execute_write.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "MERGE INTO" in sql
        assert '"AIW_INBOX_MESSAGES"' in sql
        assert params[0] == "msg-1"
        assert params[1] == "idem-1"
        assert json.loads(params[2]) == {"type": "test"}
        assert params[3] == "RECEIVED"


class TestHanaInboxRepositoryUpdateStatus:
    def test_update_status_returns_true_on_match(self):
        repo, db = _make_repo()
        db.execute_write.return_value = 1
        result = repo.update_status("msg-1", "COMPLETED", processed_at="t2")
        assert result is True
        call_args = db.execute_write.call_args
        params = call_args[0][1]
        assert params[0] == "msg-1"
        assert params[1] == "COMPLETED"

    def test_update_status_returns_false_on_no_match(self):
        repo, db = _make_repo()
        db.execute_write.return_value = 0
        result = repo.update_status("nonexistent", "COMPLETED")
        assert result is False
