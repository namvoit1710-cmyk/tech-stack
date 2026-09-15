"""The SQL for the two new tables, executed by a real SQL engine.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §13, §15.

Every other test for ``HanaLoopStateRepository`` and ``HanaRunNodeStateRepository``
uses a ``FakeDb`` that **ignores the SQL text entirely** — it zips the params onto
a hard-coded column tuple. That proves the decisions and proves nothing about the
statements. A wrong column count, a column name that does not exist, a stray
placeholder: all of them pass those suites.

And they would be invisible in production, because both writers are shadow
writes: ``_mirror_loop_state`` and ``_mirror_node_state`` swallow every exception
by design, so a broken INSERT means the table quietly stays empty — which is
exactly what it looks like when there has been no traffic. The failure mode of a
bad statement here is *indistinguishable from success*.

So these tests run the repos' real SQL against SQLite, over a schema **derived
from the shipped ``.hdbtable`` files** rather than hand-copied — a column added
to the DDL and forgotten in the INSERT fails here. Placeholders go through the
production ``HanaConnectionManager._bind_params``, the same translation
``test_hana_run_repo_cas`` uses.

SQLite is not HANA. It will not catch a HANA-specific type or a length overflow.
It does catch the whole class of "this statement was never executed".
"""
from __future__ import annotations

import json
import re
import sqlite3

import pytest

from app.layer1_domain.entities.workflow_run import RunContext
from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)
from app.layer4_frameworks.persistence.hana.hana_loop_state_repo import (
    HanaLoopStateRepository,
)
from app.layer4_frameworks.persistence.hana.hana_run_node_state_repo import (
    HanaRunNodeStateRepository,
)
from app.layer4_frameworks.persistence.hana.migrations import _MIGRATIONS_DIR

_HDI = _MIGRATIONS_DIR.parent / "hdi"

# NVARCHAR(n)/NCLOB carry no meaning in SQLite; INTEGER does. Lengths are dropped
# rather than emulated — pretending SQLite enforces NVARCHAR(128) would make this
# file claim a guarantee it cannot give (the repos truncate in Python, and those
# tests live next door).
_TYPES = (
    (re.compile(r"NVARCHAR\s*\(\s*\d+\s*\)", re.I), "TEXT"),
    (re.compile(r"\bNCLOB\b", re.I), "TEXT"),
)


def _sqlite_ddl(table: str) -> str:
    """Translate the shipped ``.hdbtable`` into a SQLite ``CREATE TABLE``.

    Derived, not transcribed: this is what makes the test notice a column that
    was added to the DDL and forgotten in the repo's INSERT.
    """
    body = (_HDI / f"{table}.hdbtable").read_text(encoding="utf-8")
    body = re.sub(r'^\s*COLUMN\s+TABLE\s+"(\w+)"', r'CREATE TABLE "\1"', body,
                  flags=re.I)
    for pattern, replacement in _TYPES:
        body = pattern.sub(replacement, body)
    return body.strip()


class SqliteHana:
    """``IDatabaseConnection`` over SQLite, running the repos' real statements."""

    def __init__(self, *tables: str) -> None:
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        for table in tables:
            self._conn.execute(_sqlite_ddl(table))
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

    def execute_write(self, sql: str, params: tuple = ()) -> int:
        translated, ordered = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(translated, ordered)
        self._conn.commit()
        return cur.rowcount or 0

    def execute_write_returning_rowcount(self, sql: str, params: tuple = ()) -> int:
        """The CAS UPDATE path needs this. Its absence is why that path went
        unexercised — ``HanaRunRepository.save`` only reaches it on the SECOND
        save of a run, and nothing here saved twice."""
        translated, ordered = HanaConnectionManager._bind_params(sql, params)
        cur = self._conn.execute(translated, ordered)
        rowcount = cur.rowcount
        self._conn.commit()
        cur.close()
        return rowcount if rowcount is not None else 0

    def raw(self, sql: str) -> list[tuple]:
        return self._conn.execute(sql).fetchall()


def _ref(file_id: str) -> dict:
    return {"__file_ref": True, "file_id": file_id, "row_count": 2}


# ───────────────────────── AIW_RUN_LOOP_STATE ────────────────────────


@pytest.fixture
def loop_repo():
    db = SqliteHana("AIW_RUN_LOOP_STATE")
    return HanaLoopStateRepository(db), db


LOOP_STATE = {
    "outer": {"total": 4, "mode": "sequential", "invocation_id": "inv-1",
              "current_index": 2, "completed_iterations": [0, 1],
              "iter_progress": {2: ["body"]},
              "items": _ref("items-file"),
              "iteration_results": _ref("results-file")},
    "inner": {"total": 2, "invocation_id": "inv-2", "items": [{"a": 1}]},
}


class TestTheLoopStateStatementsExecute:
    def test_the_insert_runs_and_the_rows_land(self, loop_repo):
        r, db = loop_repo
        assert r.save_run("run-1", LOOP_STATE) == 2
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_LOOP_STATE")[0][0] == 2

    def test_the_columns_line_up_with_the_values(self, loop_repo):
        """The failure this file exists for: a column list and a value tuple
        that agree in COUNT but not in ORDER. ``FakeDb`` zips them, so it can
        never see it — here the values have to land in the right columns."""
        r, db = loop_repo
        r.save_run("run-1", LOOP_STATE)
        row = db.raw(
            'SELECT NODE_ID, INVOCATION_ID, SEQ, TOTAL, ITEMS_REF, RESULTS_REF '
            'FROM AIW_RUN_LOOP_STATE WHERE NODE_ID = \'outer\'')[0]
        # The two ``*_REF`` columns are retired to NULL: ``NVARCHAR(128)`` holds
        # an id, and an id cannot restore a ref. The pointers ride PROGRESS_JSON.
        assert row == ("outer", "inv-1", 0, 4, None, None)
        assert json.loads(db.raw(
            'SELECT PROGRESS_JSON FROM AIW_RUN_LOOP_STATE '
            'WHERE NODE_ID = \'outer\'')[0][0])["items"]["file_id"] == "items-file"

    def test_the_full_round_trip_through_real_sql(self, loop_repo):
        r, _ = loop_repo
        r.save_run("run-1", LOOP_STATE)
        restored = r.get_run("run-1")
        assert list(restored) == ["outer", "inner"]
        assert restored["outer"]["total"] == 4
        assert restored["outer"]["iter_progress"] == {2: ["body"]}
        assert restored["inner"]["items"] == [{"a": 1}]

    def test_every_column_in_the_insert_is_actually_written(self, loop_repo):
        """Found by mutation: dropping ``UPDATED_AT`` from ``_COLUMNS`` changed
        the INSERT and the SELECT consistently, so nothing noticed. A column
        that quietly stops being written is exactly the shape this file is for,
        so each one gets asserted rather than assumed."""
        r, db = loop_repo
        r.save_run("run-1", LOOP_STATE)
        row = db.raw('SELECT RUN_ID, NODE_ID, INVOCATION_ID, SEQ, STATE_JSON, '
                     'PROGRESS_JSON, TOTAL, UPDATED_AT '
                     'FROM AIW_RUN_LOOP_STATE WHERE NODE_ID = \'outer\'')[0]
        assert all(v is not None for v in row), row
        assert row[7].startswith("20")          # UPDATED_AT is an ISO timestamp
        # ITEMS_REF / RESULTS_REF are excluded on purpose — they are retired and
        # written NULL, so asserting them non-NULL would re-pin the defect this
        # change removes. That they are still SELECTable is covered above.

    def test_order_by_seq_names_a_column_that_exists(self, loop_repo):
        """NOT that ordering depends on it — ``get_run`` re-sorts in Python on
        purpose, and mutation confirms removing the ``ORDER BY`` changes
        nothing. What this pins is that the clause is valid SQL: rename ``SEQ``
        and SQLite rejects the SELECT."""
        r, db = loop_repo
        r.save_run("run-1", LOOP_STATE)
        assert db.raw("SELECT NODE_ID FROM AIW_RUN_LOOP_STATE ORDER BY SEQ ASC") == [
            ("outer",), ("inner",)]

    def test_delete_then_insert_really_deletes(self, loop_repo):
        r, db = loop_repo
        r.save_run("run-1", LOOP_STATE)
        r.save_run("run-1", {"outer": {"total": 9}})
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_LOOP_STATE")[0][0] == 1

    def test_the_primary_key_is_real(self, loop_repo):
        """(RUN_ID, NODE_ID). Writing the same loop twice without the DELETE
        would violate it — proving the delete-then-insert is load-bearing and
        not decorative."""
        r, db = loop_repo
        r.save_run("run-1", {"a": {"total": 1}})
        with pytest.raises(sqlite3.IntegrityError):
            db.execute_write(
                'INSERT INTO AIW_RUN_LOOP_STATE (RUN_ID, NODE_ID, SEQ) '
                'VALUES (:p0, :p1, :p2)', ("run-1", "a", 0))

    def test_a_file_backed_collection_round_trips_through_real_sql(self, loop_repo):
        """Replaces ``test_refs_for_run_executes``. ``refs_for_run`` is gone: it
        had zero callers and returned an id, which cannot rebuild a ref. What
        has to execute against real SQL now is the pointer surviving the row."""
        r, _ = loop_repo
        r.save_run("run-1", LOOP_STATE)
        assert r.get_run("run-1")["outer"]["items"] == LOOP_STATE["outer"]["items"]

    def test_delete_run_executes(self, loop_repo):
        r, db = loop_repo
        r.save_run("run-1", LOOP_STATE)
        r.delete_run("run-1")
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_LOOP_STATE")[0][0] == 0


# ───────────────────────── AIW_RUN_NODE_STATE ────────────────────────


@pytest.fixture
def node_repo():
    db = SqliteHana("AIW_RUN_NODE_STATE")
    return HanaRunNodeStateRepository(db), db


def _ctx() -> RunContext:
    return RunContext(
        node_visits={"a": 3, "b": 1}, pending_revisits={"b"},
        completed_nodes={"a", "b"}, last_output_ports={"a": "success"},
    )


class TestTheNodeStateStatementsExecute:
    def test_the_insert_runs_and_the_rows_land(self, node_repo):
        r, db = node_repo
        assert r.save_run("run-1", _ctx()) == 2
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_NODE_STATE")[0][0] == 2

    def test_the_columns_line_up_with_the_values(self, node_repo):
        r, db = node_repo
        r.save_run("run-1", _ctx())
        row = db.raw(
            'SELECT NODE_ID, STATE, VISITS, PENDING_REVISIT, LAST_OUTPUT_PORT '
            'FROM AIW_RUN_NODE_STATE WHERE NODE_ID = \'a\'')[0]
        assert row == ("a", "completed", 3, 0, "success")

    def test_pending_revisit_lands_in_its_own_column(self, node_repo):
        """Migration 058's whole reason: node ``b`` is completed AND pending,
        which one ``STATE`` column cannot express."""
        r, db = node_repo
        r.save_run("run-1", _ctx())
        assert db.raw('SELECT STATE, PENDING_REVISIT FROM AIW_RUN_NODE_STATE '
                      'WHERE NODE_ID = \'b\'')[0] == ("completed", 1)

    def test_the_full_round_trip_through_real_sql(self, node_repo):
        r, _ = node_repo
        r.save_run("run-1", _ctx())
        assert r.get_run("run-1") == {
            "node_visits": {"a": 3, "b": 1}, "pending_revisits": {"b"}}

    def test_every_column_in_the_insert_is_actually_written(self, node_repo):
        r, db = node_repo
        r.save_run("run-1", _ctx())
        row = db.raw('SELECT RUN_ID, NODE_ID, STATE, VISITS, PENDING_REVISIT, '
                     'LAST_OUTPUT_PORT, UPDATED_AT FROM AIW_RUN_NODE_STATE '
                     'WHERE NODE_ID = \'a\'')[0]
        assert all(v is not None for v in row), row
        assert row[6].startswith("20")          # UPDATED_AT is an ISO timestamp

    def test_delete_then_insert_really_deletes(self, node_repo):
        r, db = node_repo
        r.save_run("run-1", _ctx())
        r.save_run("run-1", RunContext(node_visits={"a": 1}))
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_NODE_STATE")[0][0] == 1

    def test_the_primary_key_is_real(self, node_repo):
        r, db = node_repo
        r.save_run("run-1", RunContext(node_visits={"a": 1}))
        with pytest.raises(sqlite3.IntegrityError):
            db.execute_write(
                'INSERT INTO AIW_RUN_NODE_STATE (RUN_ID, NODE_ID) '
                'VALUES (:p0, :p1)', ("run-1", "a"))

    def test_delete_run_executes(self, node_repo):
        r, db = node_repo
        r.save_run("run-1", _ctx())
        r.delete_run("run-1")
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_NODE_STATE")[0][0] == 0


# ──────────────────── the schema the tests bind to ───────────────────


class TestTheSchemaComesFromTheShippedDdl:
    @pytest.mark.parametrize("table", ["AIW_RUN_LOOP_STATE", "AIW_RUN_NODE_STATE"])
    def test_it_is_derived_not_transcribed(self, table):
        """If this ever became a hand-copy, a column added to the .hdbtable and
        forgotten in the repo's INSERT would stop being caught here."""
        assert _sqlite_ddl(table).upper().startswith("CREATE TABLE")
        assert "NVARCHAR" not in _sqlite_ddl(table).upper()

    def test_the_loop_table_has_seq(self):
        assert '"SEQ"' in _sqlite_ddl("AIW_RUN_LOOP_STATE")

    def test_the_node_table_has_pending_revisit(self):
        assert '"PENDING_REVISIT"' in _sqlite_ddl("AIW_RUN_NODE_STATE")

    @pytest.mark.parametrize("table", ["AIW_RUN_LOOP_STATE", "AIW_RUN_NODE_STATE"])
    def test_sqlite_accepts_it(self, table):
        SqliteHana(table)      # a DDL this cannot parse is a DDL to look at
