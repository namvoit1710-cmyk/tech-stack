import json
from io import StringIO
from unittest.mock import MagicMock

from agent_sdk.layer1_domain.entities.outbox_record import OutboxRecord
from agent_sdk.layer4_frameworks.persistence.hana.hana_outbox_repository import (
    HanaOutboxRepository,
)


def _make_repo() -> tuple[HanaOutboxRepository, MagicMock]:
    db = MagicMock()
    repo = HanaOutboxRepository(db=db)
    return repo, db


class TestHanaOutboxRepositorySetup:
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


class TestHanaOutboxRepositorySave:
    def test_save_inserts_record(self):
        repo, db = _make_repo()
        record = OutboxRecord(
            outbox_id="out-1",
            topic="agent.responses",
            message={"status": "success"},
            key="corr-1",
            status="PENDING",
            created_at="t",
        )
        result = repo.save(record)
        assert result == record
        db.execute_write.assert_called_once()
        call_args = db.execute_write.call_args
        sql = call_args[0][0]
        params = call_args[0][1]
        assert "INSERT INTO" in sql
        assert '"AIW_OUTBOX_MESSAGES"' in sql
        assert params[0] == "out-1"
        assert params[1] == "agent.responses"
        assert json.loads(params[2]) == {"status": "success"}
        assert params[3] == "corr-1"
        assert params[4] == "PENDING"


class TestHanaOutboxRepositoryMarkPublished:
    def test_returns_true_on_match(self):
        repo, db = _make_repo()
        db.execute_write.return_value = 1
        result = repo.mark_published("out-1", "2026-01-01T00:00:01+00:00")
        assert result is True
        call_args = db.execute_write.call_args
        params = call_args[0][1]
        assert params[0] == "out-1"
        assert params[1] == "PUBLISHED"
        assert params[2] == "2026-01-01T00:00:01+00:00"

    def test_returns_false_on_no_match(self):
        repo, db = _make_repo()
        db.execute_write.return_value = 0
        result = repo.mark_published("nonexistent", "t")
        assert result is False


class TestHanaOutboxRepositoryMarkFailed:
    def test_returns_true_on_match(self):
        repo, db = _make_repo()
        db.execute_write.return_value = 1
        result = repo.mark_failed("out-1")
        assert result is True

    def test_returns_false_on_no_match(self):
        repo, db = _make_repo()
        db.execute_write.return_value = 0
        result = repo.mark_failed("nonexistent")
        assert result is False


class TestHanaOutboxRepositoryFindPending:
    def test_returns_empty_when_no_pending(self):
        repo, db = _make_repo()
        db.execute_query.return_value = []
        result = repo.find_pending()
        assert result == []

    def test_returns_pending_records(self):
        repo, db = _make_repo()
        db.execute_query.return_value = [
            {
                "outbox_id": "out-1",
                "topic": "agent.responses",
                "message": '{"status": "success"}',
                "msg_key": "corr-1",
                "status": "PENDING",
                "created_at": "t",
                "published_at": "",
                "attempts": 0,
            }
        ]
        result = repo.find_pending()
        assert len(result) == 1
        assert result[0].outbox_id == "out-1"
        assert result[0].message == {"status": "success"}

    def test_handles_lob_message(self):
        repo, db = _make_repo()
        db.execute_query.return_value = [
            {
                "outbox_id": "out-1",
                "topic": "t",
                "message": StringIO('{"k": "v"}'),
                "msg_key": "",
                "status": "PENDING",
                "created_at": "t",
                "published_at": None,
                "attempts": 0,
            }
        ]
        result = repo.find_pending()
        assert result[0].message == {"k": "v"}

    def test_passes_limit_parameter(self):
        repo, db = _make_repo()
        db.execute_query.return_value = []
        repo.find_pending(limit=10)
        call_args = db.execute_query.call_args
        params = call_args[0][1]
        assert params[0] == "PENDING"
        assert params[1] == "FAILED"
        assert params[2] == 10
