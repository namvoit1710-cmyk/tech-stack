"""``HanaNodeOutputRepository`` — the append-only descriptor store.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md``.

Driven by a fake ``IDatabaseConnection`` that keeps rows in memory and answers
the repository's own SQL shapes. That is deliberate: it exercises the real
parameter ordering and the real read-back path, so a column swapped between the
INSERT list and the SELECT list fails here rather than on tenant-1.
"""
from __future__ import annotations

import json

import pytest

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer4_frameworks.persistence.hana.hana_node_output_repo import (
    _COLUMNS,
    HanaNodeOutputRepository,
)


class FakeDb:
    """In-memory stand-in that understands only the SQL this repo issues."""

    def __init__(self):
        self.outputs: list[dict] = []
        self.schemas: dict[str, dict] = {}
        self.writes: list[str] = []
        self.queries: list[str] = []

    def execute_write(self, sql: str, params=()):
        self.writes.append(sql)
        if sql.startswith("INSERT INTO AIW_RUN_NODE_OUTPUT"):
            self.outputs.append(dict(zip(_COLUMNS, params)))
        elif sql.startswith("DELETE FROM AIW_RUN_NODE_OUTPUT"):
            if "ARTIFACT_SEQ" in sql or "ATTEMPT" in sql:
                run, node, gen, it, att = params
                self.outputs = [
                    r for r in self.outputs
                    if not (r["RUN_ID"] == run and r["NODE_ID"] == node
                            and r["GENERATION"] == gen and r["ITERATION"] == it
                            and r["ATTEMPT"] == att)
                ]
            else:
                self.outputs = [r for r in self.outputs if r["RUN_ID"] != params[0]]
        elif sql.startswith("INSERT INTO AIW_SCHEMA_DEF"):
            digest, payload, count, _created = params
            if digest in self.schemas:
                err = Exception("unique constraint violated")
                err.errorcode = 301
                raise err
            self.schemas[digest] = {"COLUMNS_JSON": payload, "COLUMN_COUNT": count}

    def execute_query(self, sql: str, params=()):
        self.queries.append(sql)
        if sql.startswith("SELECT COUNT(*) AS CNT FROM AIW_SCHEMA_DEF"):
            return [{"cnt": 1 if params[0] in self.schemas else 0}]
        if sql.startswith("SELECT SCHEMA_HASH, COLUMNS_JSON"):
            return [{"schema_hash": d, "columns_json": self.schemas[d]["COLUMNS_JSON"]}
                    for d in params if d in self.schemas]
        if sql.startswith("SELECT") and "FROM AIW_RUN_NODE_OUTPUT" in sql:
            if "IN (" in sql:                      # get_latest_many
                run, rest = params[0], params[1:]
                refs = set(rest)
                rows = [r for r in self.outputs
                        if r["RUN_ID"] == run
                        and (r["NODE_ID"] in refs or r["NODE_NAME"] in refs)]
                rows.sort(key=lambda r: (-r["GENERATION"], -r["ITERATION"],
                                         -r["ATTEMPT"], r["ARTIFACT_SEQ"]))
                return [{k.lower(): v for k, v in r.items()} for r in rows]
            run, a, b = params
            rows = [r for r in self.outputs
                    if r["RUN_ID"] == run and (r["NODE_ID"] == a or r["NODE_NAME"] == b)]
            rows.sort(key=lambda r: (-r["GENERATION"], -r["ITERATION"],
                                     -r["ATTEMPT"], r["ARTIFACT_SEQ"]))
            # mimic the driver: lowercase keys
            return [{k.lower(): v for k, v in r.items()} for r in rows]
        return []


@pytest.fixture
def repo():
    db = FakeDb()
    return HanaNodeOutputRepository(db), db


def _ref(file_id="f1", children=0):
    ref = build_file_ref(file_id=file_id, row_count=3, columns=["id", "qty"],
                         column_types={"id": "number", "qty": "number"},
                         value_kind="array", item_kind="object", size_bytes=512)
    for i in range(children):
        art = add_artifact(ref, file_id=f"{file_id}-c{i}", name=f"lines{i}",
                           artifact_type="nested_array",
                           parent_row_key_column="__parent_row_id",
                           column_types={"v": "string"}, size_bytes=64,
                           created_at="2026-01-01T00:00:00+00:00")
        art["row_count"] = 9
        art["value_kind"] = "array"
        art["item_kind"] = "object"
    return ref


class TestRoundTripThroughTheRepository:
    def test_what_goes_in_comes_back(self, repo):
        r, _db = repo
        ref = _ref(children=2)
        assert r.save(run_id="run-1", node_id="n1", node_name="Fetch", file_ref=ref) == 3
        assert r.get_latest("run-1", "n1") == ref

    def test_addressable_by_name_as_well_as_id(self, repo):
        """One stored payload, two indexes — the operator's explicit requirement,
        and the reason the node.name alias (46.9% of one real CONTEXT) can go."""
        r, _db = repo
        ref = _ref()
        r.save(run_id="run-1", node_id="n1", node_name="Fetch", file_ref=ref)
        assert r.get_latest("run-1", "Fetch") == ref
        assert r.get_latest("run-1", "n1") == r.get_latest("run-1", "Fetch")

    def test_missing_node_is_none_not_an_error(self, repo):
        r, _db = repo
        assert r.get_latest("run-1", "nope") is None


class TestNewestWins:
    def test_generation_beats_iteration_and_attempt(self, repo):
        """Re-running a run keeps its RUN_ID and bumps GENERATION (the G-guard).
        Without it in the ordering, a re-run's first attempt reads as the
        original's."""
        r, _db = repo
        old = _ref("old")
        new = _ref("new")
        r.save(run_id="run-1", node_id="n1", file_ref=old, generation=0,
               iteration=9, attempt=9)
        r.save(run_id="run-1", node_id="n1", file_ref=new, generation=1,
               iteration=0, attempt=0)
        assert r.get_latest("run-1", "n1")["file_id"] == "new"

    def test_later_attempt_wins_within_a_generation(self, repo):
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("a"), attempt=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("b"), attempt=1)
        assert r.get_latest("run-1", "n1")["file_id"] == "b"

    def test_iterations_are_kept_side_by_side(self, repo):
        """History, not overwrite — today context.variables keeps only the last."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("i0"), iteration=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("i1"), iteration=1)
        assert len({row["ITERATION"] for row in db.outputs}) == 2


class TestRewritingAnAttemptDoesNotLeaveOrphans:
    def test_a_smaller_tree_replaces_a_bigger_one_completely(self, repo):
        """The failure this prevents: children of the previous descriptor whose
        PARENT_SEQ still resolves, so rebuild returns a tree mixing both."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("big", children=3))
        assert len(db.outputs) == 4
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("small", children=0))
        assert len(db.outputs) == 1
        assert r.get_latest("run-1", "n1")["artifacts"] == []

    def test_it_only_clears_its_own_attempt(self, repo):
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("keep"), attempt=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("new"), attempt=1)
        attempts = {row["ATTEMPT"] for row in db.outputs}
        assert attempts == {0, 1}


class TestTheSchemaDictionary:
    def test_identical_schemas_are_written_once(self, repo):
        """The 5.6x. Ten nodes on one shape must not make ten dictionary rows."""
        r, db = repo
        for i in range(10):
            r.save(run_id="run-1", node_id=f"n{i}", file_ref=_ref(f"f{i}"))
        inserts = [s for s in db.writes if s.startswith("INSERT INTO AIW_SCHEMA_DEF")]
        assert len(inserts) == 1
        assert len(db.schemas) == 1

    def test_schema_rows_are_never_deleted_or_rewritten(self, repo):
        """They are shared and immutable-by-hash: deleting one orphans every
        row still pointing at it."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref())
        r.delete_run("run-1")
        assert db.schemas, "schema dictionary must survive a run delete"
        assert not any("DELETE" in s and "AIW_SCHEMA_DEF" in s for s in db.writes)

    def test_a_concurrent_duplicate_insert_is_success_not_failure(self, repo):
        """Two writers racing on the same hash converge on identical bytes;
        HANA error 301 there is a win, not an error to propagate."""
        r, db = repo
        ref = _ref()
        r.save(run_id="run-1", node_id="n1", file_ref=ref)
        db.schemas_probe_disabled = True
        # force the probe to miss so the insert races and raises 301
        original = db.execute_query

        def blind(sql, params=()):
            if sql.startswith("SELECT COUNT(*) AS CNT FROM AIW_SCHEMA_DEF"):
                return [{"cnt": 0}]
            return original(sql, params)

        db.execute_query = blind
        r.save(run_id="run-2", node_id="n1", file_ref=ref)  # must not raise
        assert r.get_latest("run-2", "n1") == ref

    def test_the_dictionary_is_read_in_one_query(self, repo):
        """A descriptor stops being ~5 KB on the row only if resolving it does
        not cost a query per artifact."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref(children=5))
        db.queries.clear()
        r.get_latest("run-1", "n1")
        schema_reads = [q for q in db.queries if "COLUMNS_JSON" in q]
        assert len(schema_reads) == 1


class TestTheAliasCannotKillARun:
    def test_an_overlong_node_name_is_truncated_not_rejected(self, repo):
        """NODE_NAME is NVARCHAR(256) and node names are user-typed with nothing
        validating length. Overflowing would fail the insert with HANA 274 and
        take the run down over a display label."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", node_name="x" * 900, file_ref=_ref())
        assert len(db.outputs[0]["NODE_NAME"]) == 256
        assert r.get_latest("run-1", "n1") is not None


class TestEmptyInput:
    def test_a_non_file_ref_writes_nothing(self, repo):
        r, db = repo
        assert r.save(run_id="run-1", node_id="n1", file_ref={"not": "a ref"}) == 0
        assert db.outputs == []


class TestOrderingDoesNotDependOnTheDatabase:
    """The first version of this file could not have caught a broken ORDER BY.

    ``FakeDb`` sorts its own rows regardless of the SQL, so an assertion about
    "newest wins" proved nothing about the query — deleting ``GENERATION DESC``
    from the statement left every test green. The fix was in the repository, not
    the fake: ``get_latest`` now picks the newest tuple in Python, so a driver,
    query-plan change or partitioned scan handing rows back in another order
    cannot silently return a different attempt's tree.
    """

    def test_newest_wins_even_when_rows_arrive_worst_first(self, repo):
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("old"), generation=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("new"), generation=2)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("mid"), generation=1)

        original = db.execute_query

        def reversed_rows(sql, params=()):
            out = original(sql, params)
            return list(reversed(out)) if "FROM AIW_RUN_NODE_OUTPUT" in sql else out

        db.execute_query = reversed_rows
        assert r.get_latest("run-1", "n1")["file_id"] == "new"

    def test_the_sql_still_sorts_so_the_db_does_the_work(self):
        from app.layer4_frameworks.persistence.hana.hana_node_output_repo import (
            _SELECT_LATEST_SQL,
        )
        assert "ORDER BY GENERATION DESC" in _SELECT_LATEST_SQL


class TestListIterations:
    """``GET /nodes/{id}/output`` renders a loop node's iterations side by side.

    Today that history only exists as one ``NodeOutputProduced`` event per
    iteration; here they are rows, so the read is a query instead of a replay.
    """

    def test_iterations_come_back_oldest_first(self, repo):
        r, _db = repo
        for i in range(4):
            r.save(run_id="run-1", node_id="n1", file_ref=_ref(f"i{i}"), iteration=i)
        got = r.list_iterations("run-1", "n1")
        assert [it for _g, it, _a, _ref in got] == [0, 1, 2, 3]
        assert [ref["file_id"] for *_x, ref in got] == ["i0", "i1", "i2", "i3"]

    def test_each_iteration_keeps_its_own_artifact_tree(self, repo):
        """The failure this guards: regrouping children under the wrong
        iteration, which produces a plausible tree that is simply not the one
        that was written."""
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("a", children=1), iteration=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("b", children=3), iteration=1)
        got = dict(((it, ref) for _g, it, _a, ref in r.list_iterations("run-1", "n1")))
        assert len(got[0]["artifacts"]) == 1
        assert len(got[1]["artifacts"]) == 3
        assert got[0] == _ref("a", children=1)

    def test_grouping_survives_rows_arriving_in_any_order(self, repo):
        r, db = repo
        for i in range(3):
            r.save(run_id="run-1", node_id="n1", file_ref=_ref(f"i{i}", children=2),
                   iteration=i)
        original = db.execute_query

        def shuffled(sql, params=()):
            out = original(sql, params)
            return list(reversed(out)) if "FROM AIW_RUN_NODE_OUTPUT" in sql else out

        db.execute_query = shuffled
        got = r.list_iterations("run-1", "n1")
        assert [it for _g, it, _a, _r in got] == [0, 1, 2]
        assert all(len(ref["artifacts"]) == 2 for *_x, ref in got)

    def test_generations_are_kept_apart(self, repo):
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("g0"), generation=0, iteration=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("g1"), generation=1, iteration=0)
        keys = [(g, i) for g, i, _a, _r in r.list_iterations("run-1", "n1")]
        assert keys == [(0, 0), (1, 0)]

    def test_addressable_by_name(self, repo):
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", node_name="Loop", file_ref=_ref(),
               iteration=0)
        assert len(r.list_iterations("run-1", "Loop")) == 1

    def test_unknown_node_is_an_empty_list(self, repo):
        r, _db = repo
        assert r.list_iterations("run-1", "nope") == []

    def test_the_dictionary_is_still_read_once(self, repo):
        """One query for the schemas across ALL iterations, not one per."""
        r, db = repo
        for i in range(5):
            r.save(run_id="run-1", node_id="n1", file_ref=_ref(f"i{i}", children=2),
                   iteration=i)
        db.queries.clear()
        r.list_iterations("run-1", "n1")
        assert len([q for q in db.queries if "COLUMNS_JSON" in q]) == 1

    def test_two_attempts_of_one_iteration_are_not_merged(self, repo):
        """Found by mutation: dropping ATTEMPT from the grouping key left every
        other test green. A retry writes a SECOND tree under the same
        (generation, iteration); merging them concatenates two descriptors'
        artifact rows into one, and ``rebuild`` returns a tree that is
        structurally valid and simply wrong."""
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("try0", children=1),
               iteration=0, attempt=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("try1", children=2),
               iteration=0, attempt=1)

        got = r.list_iterations("run-1", "n1")
        assert [(i, a) for _g, i, a, _r in got] == [(0, 0), (0, 1)]
        assert got[0][3] == _ref("try0", children=1)
        assert got[1][3] == _ref("try1", children=2)

    def test_a_retry_does_not_disturb_the_newest_read(self, repo):
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("try0"), iteration=0, attempt=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("try1"), iteration=0, attempt=1)
        assert r.get_latest("run-1", "n1")["file_id"] == "try1"


class TestGetLatestMany:
    """``/variable-suggestions`` walks every upstream node on each click.

    Today that is **N×M** file-service round-trips (11 call sites of
    ``_fetch_columns_and_preview``, each an HTTP ``query_file_data($top=5)``).
    Reading one node at a time here would only trade N HTTP calls for N SQL
    ones — the point is that it is ONE.
    """

    def test_many_nodes_one_query(self, repo):
        r, db = repo
        for i in range(6):
            r.save(run_id="run-1", node_id=f"n{i}", file_ref=_ref(f"f{i}"))
        db.queries.clear()
        got = r.get_latest_many("run-1", [f"n{i}" for i in range(6)])

        assert len(got) == 6
        table_reads = [q for q in db.queries if "FROM AIW_RUN_NODE_OUTPUT" in q]
        assert len(table_reads) == 1, f"{len(table_reads)} queries for 6 nodes"

    def test_the_schema_dictionary_is_also_one_query(self, repo):
        r, db = repo
        for i in range(6):
            r.save(run_id="run-1", node_id=f"n{i}", file_ref=_ref(f"f{i}", children=2))
        db.queries.clear()
        r.get_latest_many("run-1", [f"n{i}" for i in range(6)])
        assert len([q for q in db.queries if "COLUMNS_JSON" in q]) == 1

    def test_keyed_by_the_ref_the_caller_asked_for(self, repo):
        """A mix of ids and names must round-trip — the caller looks the result
        up by whatever it passed in."""
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", node_name="Fetch", file_ref=_ref("a"))
        r.save(run_id="run-1", node_id="n2", node_name="Map", file_ref=_ref("b"))
        got = r.get_latest_many("run-1", ["n1", "Map"])
        assert set(got) == {"n1", "Map"}
        assert got["n1"]["file_id"] == "a" and got["Map"]["file_id"] == "b"

    def test_a_node_with_no_output_is_absent_not_none(self, repo):
        """It replaces ``variables.get(uid)``, so ``.get()`` must behave the
        same — a None entry would read as "this node produced null"."""
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref())
        got = r.get_latest_many("run-1", ["n1", "n2"])
        assert "n2" not in got
        assert got.get("n2") is None

    def test_each_node_gets_its_own_newest(self, repo):
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("old1"), generation=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("new1"), generation=3)
        r.save(run_id="run-1", node_id="n2", file_ref=_ref("only2"), generation=1)
        got = r.get_latest_many("run-1", ["n1", "n2"])
        assert got["n1"]["file_id"] == "new1"
        assert got["n2"]["file_id"] == "only2"

    def test_it_agrees_with_get_latest(self, repo):
        """Two winner rules that can disagree is worse than one that is wrong."""
        r, _db = repo
        for gen in (0, 2, 1):
            r.save(run_id="run-1", node_id="n1", file_ref=_ref(f"g{gen}"), generation=gen)
        assert r.get_latest_many("run-1", ["n1"])["n1"] == r.get_latest("run-1", "n1")

    def test_artifact_trees_are_not_mixed_between_nodes(self, repo):
        r, _db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("a", children=1))
        r.save(run_id="run-1", node_id="n2", file_ref=_ref("b", children=3))
        got = r.get_latest_many("run-1", ["n1", "n2"])
        assert len(got["n1"]["artifacts"]) == 1
        assert len(got["n2"]["artifacts"]) == 3
        assert got["n1"] == _ref("a", children=1)

    def test_duplicates_and_blanks_in_the_input(self, repo):
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref())
        db.queries.clear()
        got = r.get_latest_many("run-1", ["n1", "n1", "", "n1"])
        assert set(got) == {"n1"}
        assert len([q for q in db.queries if "FROM AIW_RUN_NODE_OUTPUT" in q]) == 1

    def test_empty_input_does_not_query_at_all(self, repo):
        r, db = repo
        db.queries.clear()
        assert r.get_latest_many("run-1", []) == {}
        assert db.queries == []
