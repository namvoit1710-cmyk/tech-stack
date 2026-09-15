"""Conformance tests for ``HanaEventLogRepository`` — Phase 4b.8.

Mirrors the behavior assertions in
``test_event_log.py::TestMemoryEventLogRepository`` and
``test_sqlite_event_log_repo.py::TestSqlite*`` against the HANA backend.

The HANA repo takes an ``IDatabaseConnection`` instance; real HANA
isn't available in CI. The tests use a **SQLite-backed fake** stub
that:

- Creates an in-memory sqlite3 table with the same column layout as
  the HANA ``AIW_EVENT_LOG`` table.
- Translates ``:p0`` / ``:p1`` / … named placeholders into positional
  ``?`` placeholders using the exact helper
  ``HanaConnectionManager._bind_params`` uses in production, so the
  SQL the HANA repo emits is exercised verbatim.
- Catches ``sqlite3.IntegrityError`` on unique-constraint violation
  and raises a fake exception whose ``errorcode`` attribute is ``301``
  — the HANA error code the repo's dedupe path looks for.

This gives real storage semantics (INSERT/SELECT/DELETE, PK enforcement,
unique-index enforcement, sequence ordering) without needing HANA in
the test environment. The fake is only ~40 lines.

Additionally includes targeted tests that verify the repo emits the
expected ``:p0``-style SQL for each operation — catches regressions
in the SQL templates without depending on the fake's behavior.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)
from app.layer4_frameworks.persistence.hana.hana_event_log_repo import (
    HanaEventLogRepository,
)


# ─────────────────────── SQLite-backed HANA stub ──────────────────


class _FakeHanaUniqueViolation(Exception):
    """Fake hdbcli exception carrying HANA's unique-constraint error code."""
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.errorcode = 301  # HANA unique key violation


class _SqliteBackedHanaStub:
    """Minimal ``IDatabaseConnection`` fake routed through in-memory sqlite3.

    Accepts the same ``:p0``-style SQL that ``HanaEventLogRepository``
    emits. Translates it to positional ``?`` placeholders via
    ``HanaConnectionManager._bind_params`` — the actual helper used in
    production — so the test exercises the real parameter-translation
    path.

    Sets up the ``AIW_EVENT_LOG`` table at construction with the same
    PK and unique index as the HANA schema.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE AIW_EVENT_LOG (
                RUN_ID       TEXT    NOT NULL,
                SEQUENCE     INTEGER NOT NULL,
                EVENT_ID     TEXT    NOT NULL,
                TOPIC        TEXT,
                EVENT_TYPE   TEXT,
                TIMESTAMP    TEXT,
                PAYLOAD      TEXT,
                SOURCE       TEXT,
                CAUSATION_ID TEXT,
                PRIMARY KEY (RUN_ID, SEQUENCE)
            )
            """
        )
        self._conn.execute(
            """
            CREATE UNIQUE INDEX IDX_AIW_EVENT_LOG_EVENT_ID
                ON AIW_EVENT_LOG (RUN_ID, EVENT_ID)
            """
        )
        self._conn.commit()

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        translated_sql, ordered_params = HanaConnectionManager._bind_params(
            sql, params,
        )
        cursor = self._conn.execute(translated_sql, ordered_params)
        if cursor.description is None:
            return []
        columns = [d[0].lower() for d in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        cursor.close()
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        translated_sql, ordered_params = HanaConnectionManager._bind_params(
            sql, params,
        )
        try:
            self._conn.execute(translated_sql, ordered_params)
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            message = str(exc)
            if "UNIQUE" in message.upper():
                raise _FakeHanaUniqueViolation(message) from exc
            raise

    def close(self) -> None:
        self._conn.close()


def _envelope(run_id: str, event_id: str, topic: str = "test.topic", **extra) -> EventLogEnvelope:
    return EventLogEnvelope(
        event_id=event_id,
        run_id=run_id,
        topic=topic,
        event_type=extra.get("event_type", "TestEvent"),
        timestamp=extra.get("timestamp", "2026-04-11T00:00:00Z"),
        payload=extra.get("payload", {"k": "v"}),
        source=extra.get("source", ""),
        causation_id=extra.get("causation_id", ""),
    )


@pytest.fixture
def repo():
    stub = _SqliteBackedHanaStub()
    r = HanaEventLogRepository(stub)
    try:
        yield r
    finally:
        stub.close()


# ───────────────────── conformance mirror ─────────────────────


class TestHanaAppendAndList:
    def test_append_and_list_preserves_order(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.append(_envelope("run-1", "e3"))

        entries = repo.list_by_run("run-1")
        assert [e.event_id for e in entries] == ["e1", "e2", "e3"]

    def test_append_assigns_monotonic_sequence_per_run(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.append(_envelope("run-2", "eX"))
        repo.append(_envelope("run-1", "e3"))

        run1 = repo.list_by_run("run-1")
        run2 = repo.list_by_run("run-2")
        assert [e.sequence for e in run1] == [1, 2, 3]
        assert [e.sequence for e in run2] == [1]


class TestHanaDedupe:
    def test_dedupe_by_event_id_within_run(self, repo):
        """Unique-constraint violation → append returns False.

        The stub raises ``_FakeHanaUniqueViolation`` with
        ``errorcode=301``. The HANA repo's exception handler maps
        error code 301 to the dedupe signal.
        """
        first = repo.append(_envelope("run-1", "e1"))
        second = repo.append(_envelope("run-1", "e1"))
        assert first is True
        assert second is False
        assert len(repo.list_by_run("run-1")) == 1

    def test_dedupe_is_per_run_not_global(self, repo):
        """Same event_id in different runs is allowed."""
        assert repo.append(_envelope("run-1", "shared-id")) is True
        assert repo.append(_envelope("run-2", "shared-id")) is True
        assert len(repo.list_by_run("run-1")) == 1
        assert len(repo.list_by_run("run-2")) == 1

    def test_non_dedupe_exception_propagates(self):
        """Errors other than HANA 301 must propagate so callers see them.

        Uses a custom stub that raises an exception with a non-301
        ``errorcode``. The repo must NOT swallow it.
        """
        class _BrokenStub:
            def execute_query(self, sql, params):
                return [{"next_seq": 1}]

            def execute_write(self, sql, params):
                exc = Exception("simulated connection drop")
                exc.errorcode = 999  # some non-dedupe error code
                raise exc

        repo = HanaEventLogRepository(_BrokenStub())
        with pytest.raises(Exception, match="simulated connection drop"):
            repo.append(_envelope("run-1", "e1"))


class TestHanaClear:
    def test_clear_drops_run_and_returns_count(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.append(_envelope("run-1", "e3"))

        removed = repo.clear("run-1")
        assert removed == 3
        assert repo.list_by_run("run-1") == []

    def test_clear_returns_zero_for_unknown_run(self, repo):
        assert repo.clear("never-existed") == 0

    def test_clear_allows_reuse_of_same_sequence_numbers(self, repo):
        """After clearing a run, the next append starts at sequence 1.

        Matches the memory/SQLite backends' observable behavior — the
        ``COALESCE(MAX(SEQUENCE), 0) + 1`` pattern rises from zero
        after the cleared rows are gone.
        """
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.clear("run-1")
        repo.append(_envelope("run-1", "new-e1"))
        entries = repo.list_by_run("run-1")
        assert len(entries) == 1
        assert entries[0].sequence == 1


class TestHanaListByRunSince:
    """Phase 4c.2 method — verified on the HANA backend stub."""

    def _filled_repo(self, repo):
        repo.append(_envelope("run-1", "e1"))
        repo.append(_envelope("run-1", "e2"))
        repo.append(_envelope("run-1", "e3"))
        repo.append(_envelope("run-2", "eX"))

    def test_returns_only_events_after_min_sequence(self, repo):
        self._filled_repo(repo)
        after_one = repo.list_by_run_since("run-1", 1)
        assert [e.event_id for e in after_one] == ["e2", "e3"]

    def test_min_sequence_at_max_returns_empty(self, repo):
        self._filled_repo(repo)
        assert repo.list_by_run_since("run-1", 3) == []
        assert repo.list_by_run_since("run-1", 99) == []

    def test_min_sequence_zero_returns_everything(self, repo):
        self._filled_repo(repo)
        all_events = repo.list_by_run_since("run-1", 0)
        assert [e.event_id for e in all_events] == ["e1", "e2", "e3"]


class TestHanaCountAndPage:
    """Replay-debugger paging methods — verified on the HANA backend stub."""

    def test_count_and_page(self, repo):
        for i in range(1, 8):
            repo.append(_envelope("run-1", f"e{i}"))

        assert repo.count_by_run("run-1") == 7
        assert repo.count_by_run("missing") == 0
        assert [e.sequence for e in repo.list_by_run_page("run-1", 0, 3)] == [1, 2, 3]
        assert [e.sequence for e in repo.list_by_run_page("run-1", 3, 3)] == [4, 5, 6]
        assert [e.sequence for e in repo.list_by_run_page("run-1", 6, 3)] == [7]
        assert repo.list_by_run_page("run-1", 99, 3) == []
        assert repo.list_by_run_page("run-1", 0, 0) == []


class TestHanaEdgeCases:
    def test_empty_run_id_uses_unscoped_bucket(self, repo):
        repo.append(_envelope("", "e1"))
        repo.append(_envelope("", "e2"))
        entries = repo.list_by_run("")
        assert len(entries) == 2
        # list_by_run reconstructs empty run_id in returned envelopes
        assert all(e.run_id == "" for e in entries)

    def test_list_unknown_run_returns_empty(self, repo):
        assert repo.list_by_run("missing") == []

    def test_payload_with_nested_dict_and_list_round_trips(self, repo):
        payload = {
            "state_updates": {
                "compute-1": {
                    "output": {"result": 42},
                    "metadata": {"type": "compute"},
                }
            },
            "artifacts": [{"file_id": "f1", "row_count": 100}],
        }
        repo.append(_envelope("run-1", "e1", payload=payload))
        entries = repo.list_by_run("run-1")
        assert len(entries) == 1
        assert entries[0].payload == payload


# ───────────────────── SQL-generation smoke tests ─────────────────


class TestHanaSqlTemplates:
    """Verify the repo emits the exact ``:p0``-style SQL the connection
    manager expects. Catches regressions in the SQL templates that
    would slip through the conformance tests if the fake were silently
    forgiving."""

    def test_insert_sql_uses_named_placeholders(self):
        captured: list[tuple[str, tuple]] = []

        class _CaptureStub:
            def execute_query(self, sql, params):
                return [{"next_seq": 1}]
            def execute_write(self, sql, params):
                captured.append((sql, params))

        repo = HanaEventLogRepository(_CaptureStub())
        repo.append(_envelope("run-1", "e1", topic="t", event_type="T"))

        assert len(captured) == 1
        sql, params = captured[0]
        # The INSERT template uses :p0..:p8 (9 named placeholders)
        assert "INSERT INTO AIW_EVENT_LOG" in sql
        assert ":p0" in sql and ":p8" in sql
        # Column order must match the repo's internal layout
        assert "RUN_ID, SEQUENCE, EVENT_ID" in sql
        # Exactly 9 positional params (run_id, sequence, event_id, topic,
        # event_type, timestamp, payload, source, causation_id)
        assert len(params) == 9
        assert params[0] == "run-1"  # run_id
        assert params[1] == 1  # sequence
        assert params[2] == "e1"  # event_id

    def test_select_by_run_since_sql_uses_sequence_filter(self):
        captured: list[tuple[str, tuple]] = []

        class _CaptureStub:
            def execute_query(self, sql, params):
                captured.append((sql, params))
                return []
            def execute_write(self, sql, params):
                pass

        repo = HanaEventLogRepository(_CaptureStub())
        repo.list_by_run_since("run-1", 5)

        assert len(captured) == 1
        sql, params = captured[0]
        assert "WHERE RUN_ID = :p0 AND SEQUENCE > :p1" in sql
        assert "ORDER BY SEQUENCE" in sql
        assert params == ("run-1", 5)
