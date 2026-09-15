"""Phase M.2.1 CAS contract — HanaRunRepository.

Same contract as ``test_memory_run_repo_cas`` exercised against a
SQLite-backed HANA stub (pattern mirrors
``test_hana_event_log_repo.py``). Validates that the real SQL
templates the repo emits — ``INSERT ... VALUES (...)`` on first
persist and
``UPDATE ... SET ..., VERSION = :new WHERE ID = :id AND VERSION = :expected``
on subsequent saves — behave correctly against a row store that
enforces primary-key uniqueness and returns rowcount from conditional
updates.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.encrypted_credential import EncryptedCredential
from app.layer2_application.repositories.run_repository_interface import (
    OptimisticConcurrencyError,
)
from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)
from app.layer4_frameworks.persistence.hana.hana_run_repo import (
    HanaRunRepository,
)


class _SqliteBackedHanaStub:
    """Minimal ``IDatabaseConnection`` fake routed through SQLite.

    Creates a table with the columns the repo reads/writes, including
    ``VERSION`` (the Phase M.2.1 CAS column). Translates ``:p0`` /
    ``:p1`` placeholders to positional ``?`` via the real
    ``HanaConnectionManager._bind_params`` helper so the exact SQL
    templates the repo emits are exercised.
    """

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE AIW_WORKFLOW_RUNS (
                ID                   TEXT PRIMARY KEY,
                WORKFLOW_ID          TEXT,
                WORKFLOW_VERSION     TEXT,
                WORKFLOW_SNAPSHOT_ID TEXT             DEFAULT '',
                INPUT_DATA           TEXT,
                OUTPUT_DATA      TEXT,
                STATUS           TEXT,
                CONTEXT          TEXT,
                STARTED_AT       TEXT,
                COMPLETED_AT     TEXT,
                ERROR            TEXT,
                CORRELATION_ID   TEXT,
                CALLBACK_TOPIC   TEXT,
                PARENT_RUN_ID    TEXT,
                PARENT_NODE_ID   TEXT,
                DEPTH_LEVEL      INTEGER,
                ROOT_RUN_ID      TEXT,
                TENANT_ID        TEXT,
                IS_TEST          INTEGER,
                TEST_MODE        TEXT,
                EXECUTION_MODE   TEXT,
                VERSION          INTEGER NOT NULL DEFAULT 0,
                GENERATION       INTEGER NOT NULL DEFAULT 0,
                USER_VARIABLES   TEXT             DEFAULT '{}',
                METADATA         TEXT             DEFAULT '{}',
                TRIGGERED_BY     TEXT             DEFAULT NULL
            )
            """
        )
        self._conn.commit()

    def execute_query(self, sql: str, params: tuple = ()) -> list[dict]:
        translated, ordered = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(translated, ordered)
        if cur.description is None:
            return []
        cols = [d[0].lower() for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        return rows

    def execute_write(self, sql: str, params: tuple = ()) -> None:
        translated, ordered = HanaConnectionManager._bind_params(sql, params)
        self._conn.execute(translated, ordered)
        self._conn.commit()

    def execute_write_returning_rowcount(self, sql: str, params: tuple = ()) -> int:
        translated, ordered = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(translated, ordered)
        rowcount = cur.rowcount
        self._conn.commit()
        cur.close()
        return rowcount if rowcount is not None else 0

    def close(self) -> None:
        self._conn.close()


@pytest.fixture
def repo():
    stub = _SqliteBackedHanaStub()
    r = HanaRunRepository(stub)
    try:
        yield r
    finally:
        stub.close()


def _make_run(run_id: str = "r-1", **overrides) -> WorkflowRun:
    defaults = dict(
        id=run_id, workflow_id="wf-1", status=RunStatus.RUNNING,
    )
    defaults.update(overrides)
    return WorkflowRun(**defaults)


class TestFirstInsert:
    def test_first_save_sets_version_to_1(self, repo):
        run = _make_run(version=0)
        repo.save(run)
        assert run.version == 1
        assert repo.get_by_id("r-1").version == 1

    def test_first_save_ignores_caller_supplied_version(self, repo):
        run = _make_run(version=99)
        repo.save(run)
        assert run.version == 1


class TestMatchingVersion:
    def test_subsequent_saves_increment(self, repo):
        run = _make_run()
        repo.save(run)  # v1
        repo.save(run)  # v2
        repo.save(run)  # v3
        assert run.version == 3
        assert repo.get_by_id("r-1").version == 3

    def test_load_mutate_save(self, repo):
        initial = _make_run()
        repo.save(initial)
        reloaded = repo.get_by_id("r-1")
        assert reloaded.version == 1
        reloaded.status = RunStatus.PAUSED
        repo.save(reloaded)
        assert reloaded.version == 2
        assert repo.get_by_id("r-1").status == RunStatus.PAUSED


class TestStaleVersionConflict:
    def test_stale_save_raises(self, repo):
        """Two independent callers race. B lands first and bumps to v2;
        A's v0 save hits the CAS predicate and fails."""
        caller_a = _make_run()
        caller_b = _make_run()

        repo.save(caller_b)  # v1
        caller_b.status = RunStatus.PAUSED
        repo.save(caller_b)  # v2

        caller_a.status = RunStatus.CANCELLED
        with pytest.raises(OptimisticConcurrencyError) as excinfo:
            repo.save(caller_a)
        assert excinfo.value.run_id == "r-1"
        assert excinfo.value.expected == 0
        assert excinfo.value.actual == 2

    def test_conflict_leaves_persisted_state_intact(self, repo):
        winner = _make_run()
        repo.save(winner)
        winner.status = RunStatus.COMPLETED
        repo.save(winner)  # v2

        loser = _make_run(version=1, status=RunStatus.CANCELLED)
        with pytest.raises(OptimisticConcurrencyError):
            repo.save(loser)
        stored = repo.get_by_id("r-1")
        assert stored.status == RunStatus.COMPLETED
        assert stored.version == 2


class TestContextRoundTrip:
    """``CONTEXT`` is GONE (step 5b), so nothing round-trips through the run row.

    These tests used to assert the opposite, and the change is deliberate. The
    column was one NCLOB rewritten in full on every save — 192 MB stored, 1.58 GB
    rewritten across 43,199 saves. Its contents moved: the projector rebuilds
    ``variables``, ``completed_nodes``, ``skipped_nodes``, ``child_runs`` and the
    port mirrors from ``AIW_EVENT_LOG``; ``AIW_WORKFLOWS`` rebuilds ``topology``;
    ``AIW_RUN_LOOP_STATE`` and ``AIW_RUN_NODE_STATE`` hold the two fields that
    were not event-sourced.

    What they pin now is that the run row no longer carries any of it, so
    re-adding ``CONTEXT`` to ``_DATA_COLUMNS`` — which would write a column that
    no longer exists — fails here rather than in production.
    """

    def test_the_port_mirrors_no_longer_ride_the_run_row(self, repo):
        """They are event-sourced: ``process_successors`` reads them off the
        projector view, which is authoritative. The regression this test was
        born from (a resumed HUMAN_ACTION traversing BOTH branches because the
        mirrors came back empty) is now the projector's to prevent, not this
        column's."""
        run = _make_run()
        run.context.last_output_ports["ha-node"] = "confirm"
        run.context.last_taken_edge_ports["ha-node"] = {"edge-1": "confirm"}
        repo.save(run)

        reloaded = repo.get_by_id("r-1")
        assert reloaded.context.last_output_ports == {}
        assert reloaded.context.last_taken_edge_ports == {}

    def test_missing_fields_in_legacy_rows_default_empty(self, repo):
        # Rows persisted before the fix carry no routing-state keys —
        # reload must default them to empty maps, not crash.
        run = _make_run()
        repo.save(run)
        reloaded = repo.get_by_id("r-1")
        assert reloaded.context.last_output_ports == {}
        assert reloaded.context.last_taken_edge_ports == {}

    def test_credentials_are_no_longer_persisted_at_all(self, repo):
        """``__credentials`` is not stored anywhere on the run any more (§13.6).

        It was the LAST thing keeping ``CONTEXT`` alive: not event-sourced —
        LAI-19 keeps the sealed map out of any replayable store — and no new
        table may hold it. Phase 1 removed all three writers instead; a node
        that declares a ``credential_ref`` gets it resolved from the vault when
        it dispatches (SA-2287 / B3).

        This test guarded a real tenant-1 401 (a stringified dataclass the TASK
        handler could not reconstruct). That failure mode is gone with the
        column, and what is guarded now is that nothing quietly starts writing a
        secret into the run row again."""
        run = _make_run()
        run.context.variables["__credentials"] = EncryptedCredential(
            ciphertext_b64="Zm9vYmFy", key_version=1,
        )
        repo.save(run)

        reloaded = repo.get_by_id("r-1")
        assert "__credentials" not in reloaded.context.variables
        # Belt and braces: no secret material anywhere in the stored row.
        stored = repo._execute_query("SELECT * FROM AIW_WORKFLOW_RUNS WHERE ID = :p0",
                                     ("r-1",))[0]
        assert "Zm9vYmFy" not in str(stored)
