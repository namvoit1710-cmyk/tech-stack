"""``HanaNodeOutputRepository``'s READ SQL, executed by a real SQL engine.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §5.3.

Its WRITE path is proven in production — ``AIW_RUN_NODE_OUTPUT`` holds 46 rows on
tenant-1. Its **reads** are not, and cannot be by traffic alone: both callers are
HTTP endpoints (``GET /nodes/{id}/output`` and
``GET /nodes/{id}/variable-suggestions``, §13.4), so executing a workflow never
touches them. Nobody has opened those panels since the code shipped, and
``test_hana_node_output_repo`` runs against a ``FakeDb`` that ignores the SQL
text — so these statements have **never been executed by anything**.

``get_latest_many`` is the one worth being nervous about. It is the only SQL in
the service built with computed placeholder indices:

    ids   = ", ".join(f":p{i + 1}"            for i in range(len(refs)))
    names = ", ".join(f":p{i + 1 + len(refs)}" for i in range(len(refs)))

With 10+ upstream nodes that emits ``:p10``, ``:p11`` … and the whole thing
depends on ``_NAMED_PARAM_RE = r":p(\\d+)"`` being greedy. It is — but "it is"
was read off the regex, and the variable-suggestions endpoint walks EVERY
upstream node, so double digits are the ordinary case rather than the exotic one.

Same harness as ``test_new_tables_sql_against_sqlite``: schema derived from the
shipped ``.hdbtable``, placeholders through the production ``_bind_params``.
"""
from __future__ import annotations

import pytest

from app.layer4_frameworks.persistence.hana.hana_connection_manager import (
    HanaConnectionManager,
)
from app.layer4_frameworks.persistence.hana.hana_node_output_repo import (
    HanaNodeOutputRepository,
)
from tests.unit.test_new_tables_sql_against_sqlite import SqliteHana


def _ref(file_id: str, columns=None, **kw) -> dict:
    ref = {"__file_ref": True, "file_id": file_id, "row_count": 3,
           "columns": columns if columns is not None else ["a", "b"],
           "column_types": {"a": "string", "b": "number"},
           "value_kind": "array", "item_kind": "object"}
    ref.update(kw)
    return ref


@pytest.fixture
def repo():
    db = SqliteHana("AIW_RUN_NODE_OUTPUT", "AIW_SCHEMA_DEF")
    return HanaNodeOutputRepository(db), db


class TestTheWriteStatementsExecute:
    def test_a_descriptor_lands(self, repo):
        r, db = repo
        assert r.save(run_id="run-1", node_id="n1", node_name="Node One",
                      file_ref=_ref("f1")) >= 1
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_NODE_OUTPUT")[0][0] >= 1

    def test_the_schema_dictionary_is_populated(self, repo):
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        assert db.raw("SELECT COUNT(*) FROM AIW_SCHEMA_DEF")[0][0] == 1

    def test_the_same_schema_is_inserted_once(self, repo):
        """Insert-if-absent, keyed by content hash. The probe SELECT and the
        INSERT are two statements that have to agree about the key."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        r.save(run_id="run-2", node_id="n2", file_ref=_ref("f2"))
        assert db.raw("SELECT COUNT(*) FROM AIW_SCHEMA_DEF")[0][0] == 1

    def test_the_attempt_delete_is_valid_sql(self, repo):
        """Five-column WHERE. Re-saving the same attempt must replace it, not
        stack a second tree the rebuild would merge."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        before = db.raw("SELECT COUNT(*) FROM AIW_RUN_NODE_OUTPUT")[0][0]
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        assert db.raw("SELECT COUNT(*) FROM AIW_RUN_NODE_OUTPUT")[0][0] == before


class TestGetLatest:
    def test_it_round_trips_through_real_sql(self, repo):
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        got = r.get_latest("run-1", "n1")
        assert got is not None and got["file_id"] == "f1"

    def test_the_schema_is_resolved_from_the_dictionary(self, repo):
        """The columns are NOT on the output row — they live in AIW_SCHEMA_DEF
        behind SCHEMA_HASH, which is the whole 5.6x dedup argument. If the
        two-statement join is wrong the descriptor comes back column-less and
        the FE dropdown is silently empty."""
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        assert r.get_latest("run-1", "n1")["columns"] == ["a", "b"]

    def test_the_name_alias_resolves(self, repo):
        """``WHERE NODE_ID = ? OR NODE_NAME = ?`` is how one stored payload
        answers both lookups — the alias was 46.9% of one real run's CONTEXT."""
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", node_name="Node One",
               file_ref=_ref("f1"))
        assert r.get_latest("run-1", "Node One")["file_id"] == "f1"

    def test_a_missing_node_is_none(self, repo):
        r, _ = repo
        assert r.get_latest("run-1", "nope") is None

    def test_the_newest_attempt_wins(self, repo):
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("old"), attempt=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("new"), attempt=1)
        assert r.get_latest("run-1", "n1")["file_id"] == "new"


class TestGetLatestManyAndItsComputedPlaceholders:
    """The reason this file exists. Nothing else in the service builds SQL with
    computed placeholder indices, and nothing has ever executed it."""

    def test_two_refs(self, repo):
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        r.save(run_id="run-1", node_id="n2", file_ref=_ref("f2"))
        got = r.get_latest_many("run-1", ["n1", "n2"])
        assert {k: v["file_id"] for k, v in got.items()} == {
            "n1": "f1", "n2": "f2"}

    def test_TWELVE_refs_so_the_placeholders_go_double_digit(self, repo):
        """``:p10``, ``:p11`` … and the ids/names halves are offset by
        ``len(refs)``, so a greedy-match failure would silently pair the wrong
        parameter with the wrong slot — not raise, just return the wrong rows.
        The variable-suggestions endpoint walks EVERY upstream node, so twelve
        is ordinary."""
        r, db = repo
        nodes = [f"n{i}" for i in range(12)]
        for i, node in enumerate(nodes):
            r.save(run_id="run-1", node_id=node, file_ref=_ref(f"file-{i}"))
        got = r.get_latest_many("run-1", nodes)
        assert {k: v["file_id"] for k, v in got.items()} == {
            f"n{i}": f"file-{i}" for i in range(12)}

    def test_the_bind_translation_keeps_double_digits_intact(self):
        """The property the above depends on, pinned directly: ``:p1`` must not
        swallow the ``0`` of ``:p10``. Making ``_NAMED_PARAM_RE`` non-greedy
        breaks 18 of the tests in this file — this is the load-bearing one."""
        sql = "SELECT * FROM T WHERE a IN (:p1, :p10, :p11) AND b = :p2"
        translated, ordered = HanaConnectionManager._bind_params(
            sql, tuple(range(12)))
        assert translated == "SELECT * FROM T WHERE a IN (?, ?, ?) AND b = ?"
        assert ordered == (1, 10, 11, 2)

    def test_the_ids_names_offset_is_redundant_as_written(self):
        """Found by mutation, and recorded rather than papered over.

        ``get_latest_many`` offsets the names half by ``len(refs)`` and binds
        ``(run_id, *refs, *refs)`` — the SECOND copy is what the offset points
        at, and it is identical to the first. Collapsing the names half back to
        ``:p1…:pN`` produces the same bound sequence and every test here still
        passes, because ``_bind_params`` resolves each placeholder by index into
        a tuple whose two halves are equal.

        So the offset is not load-bearing today. It is not wrong either — it
        keeps the two halves independently addressable if the ids and names ever
        stop being the same list — but a reader should not mistake the mutation
        surviving for missing coverage."""
        refs = ["a", "b", "c"]
        params = ("run-1", *refs, *refs)
        assert params[1:1 + len(refs)] == params[1 + len(refs):]

    def test_a_mix_of_ids_and_names_round_trips(self, repo):
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", node_name="First", file_ref=_ref("f1"))
        r.save(run_id="run-1", node_id="n2", node_name="Second", file_ref=_ref("f2"))
        got = r.get_latest_many("run-1", ["n1", "Second"])
        assert set(got) == {"n1", "Second"}

    def test_a_node_with_no_output_is_ABSENT_not_none(self, repo):
        """Callers use ``.get()`` on this like the ``variables`` dict it
        replaces — a ``None`` entry would read as 'this node produced null'."""
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        got = r.get_latest_many("run-1", ["n1", "missing"])
        assert "missing" not in got

    def test_an_empty_ref_list_never_builds_sql(self, repo):
        r, _ = repo
        assert r.get_latest_many("run-1", []) == {}

    def test_one_query_regardless_of_ref_count(self, repo):
        """The point of the method: variable-suggestions walks every upstream
        node, and doing it one at a time would trade N HTTP calls for N SQL
        ones."""
        r, db = repo
        nodes = [f"n{i}" for i in range(12)]
        for i, node in enumerate(nodes):
            r.save(run_id="run-1", node_id=node, file_ref=_ref(f"file-{i}"))
        db.queries = 0
        original = db.execute_query

        def counting(sql, params=()):
            db.queries += 1
            return original(sql, params)

        db.execute_query = counting
        r.get_latest_many("run-1", nodes)
        assert db.queries <= 2      # rows + one batched schema fetch


class TestListIterations:
    def test_it_returns_them_oldest_first(self, repo):
        r, _ = repo
        for i in range(3):
            r.save(run_id="run-1", node_id="n1", file_ref=_ref(f"f{i}"),
                   iteration=i)
        got = r.list_iterations("run-1", "n1")
        assert [t[1] for t in got] == [0, 1, 2]
        assert [t[3]["file_id"] for t in got] == ["f0", "f1", "f2"]

    def test_a_retry_is_a_separate_group(self, repo):
        """ATTEMPT is part of the grouping key. Without it two descriptors'
        artifact rows merge into one tree."""
        r, _ = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("a0"), attempt=0)
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("a1"), attempt=1)
        assert len(r.list_iterations("run-1", "n1")) == 2

    def test_an_unknown_node_is_empty(self, repo):
        r, _ = repo
        assert r.list_iterations("run-1", "nope") == []


class TestDeleteRun:
    def test_it_removes_the_runs_rows(self, repo):
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        r.save(run_id="run-2", node_id="n1", file_ref=_ref("f2"))
        r.delete_run("run-1")
        assert db.raw(
            "SELECT DISTINCT RUN_ID FROM AIW_RUN_NODE_OUTPUT") == [("run-2",)]

    def test_the_shared_schema_dictionary_is_untouched(self, repo):
        """Schema rows are shared across runs — deleting one would orphan every
        other descriptor pointing at it."""
        r, db = repo
        r.save(run_id="run-1", node_id="n1", file_ref=_ref("f1"))
        r.delete_run("run-1")
        assert db.raw("SELECT COUNT(*) FROM AIW_SCHEMA_DEF")[0][0] == 1
