"""Conformance tests for ``HanaSnapshotRepository`` — Phase 4.5.

Mirrors ``test_snapshot_repository.py`` (memory impl) against the HANA
backend. Real HANA isn't available in CI — the stub routes ``:pN``-
placeholder SQL through an in-memory sqlite3 table with the same
column layout and primary key as migration ``028_create_aiw_run_snapshots.sql``.

Parameter translation uses the exact helper
``HanaConnectionManager._bind_params`` used in production, so the SQL
the repo emits is exercised verbatim.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.layer2_application.interfaces.snapshot_repository_interface import (
    Snapshot,
)
from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)
from app.layer4_frameworks.persistence.hana.hana_snapshot_repository import (
    HanaSnapshotRepository,
)


class _SqliteBackedHanaStub:
    """Minimal ``IDatabaseConnection`` fake routed through in-memory sqlite3."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE AIW_RUN_SNAPSHOTS (
                RUN_ID         TEXT    NOT NULL,
                SEQUENCE       INTEGER NOT NULL,
                SCHEMA_VERSION INTEGER NOT NULL,
                STATE_JSON     TEXT    NOT NULL,
                CREATED_AT     TEXT    NOT NULL,
                PRIMARY KEY (RUN_ID, SEQUENCE)
            )
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
        self._conn.execute(translated_sql, ordered_params)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _snap(
    run_id: str = "r1",
    sequence: int = 1,
    schema_version: int = 1,
    state_json: str = "{}",
    created_at: str = "2026-04-22T00:00:00Z",
) -> Snapshot:
    return Snapshot(
        run_id=run_id,
        sequence=sequence,
        schema_version=schema_version,
        state_json=state_json,
        created_at=created_at,
    )


@pytest.fixture
def repo():
    stub = _SqliteBackedHanaStub()
    r = HanaSnapshotRepository(stub)
    try:
        yield r
    finally:
        stub.close()


class TestHanaSave:
    def test_save_persists(self, repo):
        repo.save(_snap(sequence=10))
        got = repo.load_latest("r1", min_schema_version=1)
        assert got is not None
        assert got.sequence == 10

    def test_save_is_idempotent_upsert(self, repo):
        repo.save(_snap(sequence=10, state_json="{}"))
        repo.save(_snap(sequence=10, state_json='{"updated":true}'))
        got = repo.load_latest("r1", min_schema_version=1)
        assert got.state_json == '{"updated":true}'

    def test_save_distinct_sequences_coexist(self, repo):
        repo.save(_snap(sequence=5))
        repo.save(_snap(sequence=10))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10


class TestHanaLoadLatest:
    def test_returns_none_when_empty(self, repo):
        assert repo.load_latest("r1", min_schema_version=1) is None

    def test_returns_max_sequence(self, repo):
        repo.save(_snap(sequence=3))
        repo.save(_snap(sequence=10))
        repo.save(_snap(sequence=7))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10

    def test_filters_below_min_schema_version(self, repo):
        repo.save(_snap(sequence=10, schema_version=1))
        assert repo.load_latest("r1", min_schema_version=2) is None

    def test_scoped_by_run_id(self, repo):
        repo.save(_snap(run_id="r1", sequence=10))
        repo.save(_snap(run_id="r2", sequence=20))
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10
        assert repo.load_latest("r2", min_schema_version=1).sequence == 20


class TestHanaDeleteOlderThan:
    def test_drops_strictly_older(self, repo):
        repo.save(_snap(sequence=5))
        repo.save(_snap(sequence=10))
        repo.save(_snap(sequence=15))
        removed = repo.delete_older_than("r1", keep_sequence=10)
        assert removed == 1
        assert repo.load_latest("r1", min_schema_version=1).sequence == 15

    def test_keep_boundary_inclusive(self, repo):
        repo.save(_snap(sequence=10))
        assert repo.delete_older_than("r1", keep_sequence=10) == 0
        assert repo.load_latest("r1", min_schema_version=1).sequence == 10

    def test_noop_on_unknown_run(self, repo):
        assert repo.delete_older_than("ghost", keep_sequence=0) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
