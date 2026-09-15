"""SA-2050 (R3) — HanaWorkerRegistryMirrorRepository against a SQLite-backed HANA
stub (no real HANA). Mirrors the hana_event_log_repo test pattern: a tiny
in-memory sqlite table with the same columns, ``:pN`` translated by the real
``HanaConnectionManager._bind_params`` so the repo's SQL is exercised verbatim.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)
from app.layer2_application.interfaces.worker_registry_mirror_repository_interface import (
    ReconcileResult,
)
from app.layer4_frameworks.persistence.hana.hana_worker_registry_mirror_repo import (
    HanaWorkerRegistryMirrorRepository,
)


class _SqliteBackedHanaStub:
    """Minimal IDatabaseConnection fake routed through in-memory sqlite3.

    Accepts the ``:pN`` SQL the repo emits, translating it to positional ``?``
    via ``HanaConnectionManager._bind_params`` (the real production helper).
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE AIW_WORKER_REGISTRY_MIRROR (
                ID             TEXT NOT NULL,
                INSTANCE_ID    TEXT,
                WORKER_TYPE    TEXT,
                HEALTH_STATUS  TEXT,
                LAST_HEARTBEAT TEXT,
                ENDPOINT_URL   TEXT,
                VERSION        TEXT,
                SPEC_VERSION   TEXT,
                KIND           TEXT,
                HOST           TEXT,
                PID            INTEGER,
                STARTED_AT     TEXT,
                FIRST_SEEN_AT  TEXT,
                LAST_SEEN_AT   TEXT,
                PRIMARY KEY (ID)
            )
            """
        )
        self._conn.commit()

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        tsql, oparams = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(tsql, oparams)
        if cur.description is None:
            return []
        cols = [d[0].lower() for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close()
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        tsql, oparams = HanaConnectionManager._bind_params(sql, params)
        self._conn.execute(tsql, oparams)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _worker(wid: str, worker_type: str = "jira_search", **extra) -> dict:
    base = {
        "id": wid,
        "worker_type": worker_type,
        "health_status": "healthy",
        "last_heartbeat": "2026-07-21T00:00:00Z",
        "endpoint_url": "http://w:8080",
        "version": "1.0.0",
        "spec_version": "1",
        "kind": "external",
    }
    base.update(extra)
    return base


@pytest.fixture
def repo():
    stub = _SqliteBackedHanaStub()
    r = HanaWorkerRegistryMirrorRepository(stub)
    try:
        yield r
    finally:
        stub.close()


class TestReconcileUpsert:
    def test_upsert_inserts_present_rows(self, repo):
        result = repo.reconcile([_worker("i1:jira_search"), _worker("i2:jira_search")])
        assert result.upserted == 2
        rows = {r["id"]: r for r in repo.get_all()}
        assert set(rows) == {"i1:jira_search", "i2:jira_search"}
        assert rows["i1:jira_search"]["worker_type"] == "jira_search"
        assert rows["i1:jira_search"]["health_status"] == "healthy"

    def test_reupsert_preserves_first_seen_updates_last_seen(self, repo):
        repo.reconcile([_worker("i1:jira_search")])
        first = repo.get_all()[0]
        repo.reconcile([_worker("i1:jira_search", health_status="unhealthy")])
        second = repo.get_all()[0]
        assert second["first_seen_at"] == first["first_seen_at"]  # preserved on re-upsert
        assert second["health_status"] == "unhealthy"             # value updated
        assert second["last_seen_at"] >= first["last_seen_at"]    # advanced

    def test_derives_instance_id_prod_and_local(self, repo):
        repo.reconcile([_worker("inst-7:jira_search"), _worker("uuid-no-colon")])
        rows = {r["id"]: r for r in repo.get_all()}
        assert rows["inst-7:jira_search"]["instance_id"] == "inst-7"   # prod: strip :worker_type
        assert rows["uuid-no-colon"]["instance_id"] == "uuid-no-colon"  # local uuid: id itself

    def test_defensive_missing_host_pid_started_at_stored_null(self, repo):
        repo.reconcile([_worker("i1:jira_search")])  # no host/pid/started_at keys
        row = repo.get_all()[0]
        assert row["host"] is None
        assert row["pid"] is None
        assert row["started_at"] is None

    def test_pid_coerced_to_int(self, repo):
        repo.reconcile([_worker("i1:jira_search", pid="4321")])
        assert repo.get_all()[0]["pid"] == 4321

    def test_empty_rows_is_noop(self, repo):
        repo.reconcile([_worker("i1:jira_search")])
        result = repo.reconcile([])
        assert result == ReconcileResult(upserted=0, deleted=0)
        assert {r["id"] for r in repo.get_all()} == {"i1:jira_search"}  # not wiped


class TestReconcileDeleteAbsent:
    def test_absent_rows_are_deleted(self, repo):
        repo.reconcile([_worker("i1:jira_search"), _worker("i2:jira_search")])
        result = repo.reconcile([_worker("i1:jira_search")])  # i2 dropped from snapshot
        assert result.deleted == 1
        assert {r["id"] for r in repo.get_all()} == {"i1:jira_search"}

    def test_empty_snapshot_never_deletes(self, repo):
        repo.reconcile([_worker("i1:jira_search"), _worker("i2:jira_search")])
        repo.reconcile([])  # empty (executor blip OR genuinely empty) — never wipe
        assert {r["id"] for r in repo.get_all()} == {"i1:jira_search", "i2:jira_search"}
