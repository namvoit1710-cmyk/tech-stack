"""``flatten``/``rebuild`` must be an exact round-trip on real descriptor shapes.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §3.2, §5.

This is the mapping the whole redesign rests on: if a descriptor does not come
back byte-identical, every downstream reader sees a different value than the
producer wrote — and `_reconstruct_recursive` will rebuild the wrong tree
without raising. Round-trip equality is therefore the assertion, not
field-by-field spot checks.

The shapes below are taken from what `_store_rows_recursive` and `add_artifact`
actually emit, including the fields they stamp on afterwards.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import (
    add_artifact,
    build_file_ref,
    schema_hash,
)
from app.layer2_application.data.node_output_rows import flatten, rebuild


def _roundtrip(ref):
    rows, schemas = flatten(ref)
    return rebuild(rows, schemas)


def _nested(depth: int):
    """A root with a single chain of nested_array children `depth` levels deep —
    the shape `_store_rows_recursive` produces for arrays inside arrays."""
    root = build_file_ref(
        file_id="root", row_count=3, columns=["id", "lines"],
        column_types={"id": "number"}, value_kind="array", item_kind="object",
        size_bytes=1234,
    )
    parent = root
    for level in range(depth):
        art = add_artifact(
            parent, file_id=f"child-{level}", name=f"lines{level}",
            artifact_type="nested_array", parent_row_key_column="__parent_row_id",
            column_types={f"c{level}": "string"}, size_bytes=10 * (level + 1),
            created_at="2026-01-01T00:00:00+00:00",
        )
        art["row_count"] = 7 + level
        art["value_kind"] = "array"
        art["item_kind"] = "object"
        parent = art
    return root


class TestRoundTrip:
    def test_bare_root(self):
        ref = build_file_ref(file_id="f1", row_count=2, columns=["a"],
                             column_types={"a": "string"}, value_kind="array",
                             item_kind="object", size_bytes=99)
        assert _roundtrip(ref) == ref

    def test_root_with_no_schema_at_all(self):
        """A value-mode CSV or empty payload — schema_hash is "" and the join
        finds no row. Must still come back as itself."""
        ref = build_file_ref(file_id="", row_count=0)
        assert _roundtrip(ref) == ref

    @pytest.mark.parametrize("depth", [1, 2, 3, 6])
    def test_nested_chain(self, depth):
        """Observed depth on tenant-1 reaches 6, and the deep tail is not rare."""
        ref = _nested(depth)
        assert _roundtrip(ref) == ref

    def test_many_siblings_keep_their_order(self):
        """`_reconstruct_recursive` walks `artifacts` positionally, so ORDINAL
        has to bring them back in the written order."""
        root = build_file_ref(file_id="root", columns=["x"], column_types={"x": "string"})
        for i in range(6):
            add_artifact(root, file_id=f"c{i}", name=f"n{i}",
                         artifact_type="nested_array",
                         parent_row_key_column="__parent_row_id",
                         created_at="2026-01-01T00:00:00+00:00")
        out = _roundtrip(root)
        assert [a["name"] for a in out["artifacts"]] == [f"n{i}" for i in range(6)]
        assert out == root

    def test_wide_tree(self):
        """Branching, not just a chain — two children each with two of their own."""
        root = build_file_ref(file_id="root", columns=["x"], column_types={"x": "string"})
        for i in range(2):
            a = add_artifact(root, file_id=f"a{i}", name=f"a{i}",
                             artifact_type="nested_array",
                             parent_row_key_column="__parent_row_id",
                             created_at="2026-01-01T00:00:00+00:00")
            for j in range(2):
                add_artifact(a, file_id=f"b{i}{j}", name=f"b{i}{j}",
                             artifact_type="nested_array",
                             parent_row_key_column="__parent_row_id",
                             created_at="2026-01-01T00:00:00+00:00")
        assert _roundtrip(root) == root


class TestTheCollisionThatForcedAdjacency:
    """`artifact_id` is RELATIVE and collides — 55 distinct ids across 2,688
    children on tenant-1. Keying on it would merge different artifacts; keying
    on ARTIFACT_SEQ must not."""

    def test_siblings_sharing_an_artifact_id_stay_distinct(self):
        root = build_file_ref(file_id="root", columns=["x"], column_types={"x": "string"})
        for i in range(3):
            add_artifact(root, file_id=f"file-{i}", name="lines",
                         artifact_id="lines", artifact_type="nested_array",
                         parent_row_key_column="__parent_row_id",
                         created_at="2026-01-01T00:00:00+00:00")
        out = _roundtrip(root)
        assert [a["file_id"] for a in out["artifacts"]] == ["file-0", "file-1", "file-2"]
        assert out == root

    def test_a_child_deep_down_can_reuse_an_ancestors_id(self):
        ref = _nested(3)
        for node, _ in ((ref, 0),):
            pass
        # force every level to share one relative id
        def walk(n):
            for a in n.get("artifacts", []):
                a["artifact_id"] = "lines"
                walk(a)
        walk(ref)
        assert _roundtrip(ref) == ref


class TestSchemaDictionary:
    def test_identical_schemas_dedupe_to_one_entry(self):
        """The 5.6x. Two artifacts with the same column_types must yield ONE
        AIW_SCHEMA_DEF row."""
        root = build_file_ref(file_id="root", columns=["x"], column_types={"x": "string"})
        for i in range(4):
            add_artifact(root, file_id=f"c{i}", name=f"n{i}",
                         artifact_type="nested_array",
                         parent_row_key_column="__parent_row_id",
                         column_types={"same": "number"},
                         created_at="2026-01-01T00:00:00+00:00")
        _rows, schemas = flatten(root)
        # root's own (columns=["x"], {"x":"string"}) + the shared child schema
        assert len(schemas) == 2

    def test_schema_hash_on_the_row_matches_the_dictionary_key(self):
        root = build_file_ref(file_id="root", columns=["a", "b"],
                              column_types={"b": "number", "a": "string"})
        rows, schemas = flatten(root)
        digest = rows[0]["SCHEMA_HASH"]
        assert digest == schema_hash(["a", "b"], {"a": "string", "b": "number"})
        assert schemas[digest]["columns"] == ["a", "b"]

    def test_an_artifact_hashes_types_only_never_the_roots_columns(self):
        """An artifact declares column_types but no `columns`. Folding the
        root's columns into its hash would give two different files one row."""
        root = build_file_ref(file_id="root", columns=["x"], column_types={"k": "string"})
        art = add_artifact(root, file_id="c", name="n", artifact_type="nested_array",
                           column_types={"k": "string"},
                           created_at="2026-01-01T00:00:00+00:00")
        rows, _ = flatten(root)
        assert rows[0]["SCHEMA_HASH"] != rows[1]["SCHEMA_HASH"]
        assert rows[1]["SCHEMA_HASH"] == schema_hash(None, {"k": "string"})
        assert "columns" not in art


class TestAdjacencyShape:
    def test_root_has_no_parent_and_depth_zero(self):
        rows, _ = flatten(_nested(2))
        assert rows[0]["PARENT_SEQ"] is None and rows[0]["DEPTH"] == 0
        assert all(r["PARENT_SEQ"] is not None for r in rows[1:])

    def test_every_parent_seq_precedes_its_child(self):
        """Depth-first numbering is what lets rebuild work in one pass."""
        rows, _ = flatten(_nested(6))
        for r in rows[1:]:
            assert int(r["PARENT_SEQ"]) < int(r["ARTIFACT_SEQ"])

    def test_column_widths_do_not_grow_with_depth(self):
        """The whole reason for adjacency over a materialised path: at depth 6
        the path column must not be six segments long."""
        shallow, _ = flatten(_nested(1))
        deep, _ = flatten(_nested(6))
        assert max(len(r["ARTIFACT_PATH"]) for r in deep) <= \
            max(len(r["ARTIFACT_PATH"]) for r in shallow) + 1

    def test_not_a_file_ref_yields_nothing(self):
        assert flatten({"just": "a dict"}) == ([], {})
        assert rebuild([], {}) is None


class TestTheShapeProductionActuallyWrites:
    """Found by round-tripping live tenant-1 descriptors, not by imagining shapes.

    Two defects surfaced there, and neither synthetic test caught them:

    1. Real artifacts DO carry ``columns``. ``add_artifact`` never sets the key,
       so an earlier version hashed artifacts on ``column_types`` alone and
       dropped their columns on the way back — losing the CHILD table's header,
       which is the input ``get_variable_suggestions_use_case.py:522`` turns
       into a silently empty dropdown.

    2. ``schema_hash`` was computed at ``add_artifact`` time while ``columns``
       is stamped on afterwards, so the stored pointer did not cover the
       artifact's real schema. Of the live artifacts carrying ``columns``,
       **13 had an EMPTY ``column_types``** — their creation-time hash was
       ``""``, i.e. no dictionary entry at all.
    """

    @staticmethod
    def _production_shape():
        """A ``get_comments``-style output: root -> 4 children -> grandchild,
        every artifact carrying its own ``columns``."""
        from app.layer1_domain.entities.data_reference import refresh_schema_hash
        root = build_file_ref(
            file_id="file_root", row_count=1, columns=["metadata", "raw"],
            column_types={"metadata": "json"}, value_kind="object",
            size_bytes=8_192,
        )
        for name in ("metadata.comments", "metadata.raw.comments",
                     "comments", "raw.comments"):
            art = add_artifact(
                root, file_id=f"file_{name}", name=name,
                artifact_type="nested_array",
                parent_row_key_column="__parent_row_id",
                column_types={"id": "string", "body.content": "string"},
                created_at="2026-08-07T04:13:38+00:00", size_bytes=1_024,
            )
            art["columns"] = ["id", "body.content"]        # stamped AFTER
            refresh_schema_hash(art)
            art["row_count"] = 4
            art["value_kind"] = "array"
            art["item_kind"] = "object"
            leaf = add_artifact(
                art, file_id=f"file_{name}_content", name="body.content",
                artifact_type="nested_array",
                parent_row_key_column="__parent_row_id",
                column_types={}, created_at="2026-08-07T04:13:38+00:00",
            )
            # The 13 live artifacts that broke it: columns present, types EMPTY.
            leaf["columns"] = ["amount", "city", "invoice_no"]
            refresh_schema_hash(leaf)
        return root

    def test_it_round_trips_exactly(self):
        ref = self._production_shape()
        assert _roundtrip(ref) == ref

    def test_artifact_columns_survive(self):
        out = _roundtrip(self._production_shape())
        assert out["artifacts"][0]["columns"] == ["id", "body.content"]
        assert out["artifacts"][0]["artifacts"][0]["columns"] == [
            "amount", "city", "invoice_no"]

    def test_columns_without_column_types_still_get_a_schema_row(self):
        """The 13. ``schema_hash(None, {})`` is "" — no dictionary entry — so
        the child table's header would vanish."""
        ref = self._production_shape()
        leaf = ref["artifacts"][0]["artifacts"][0]
        assert leaf["column_types"] == {}
        assert leaf["schema_hash"] != "", "columns-only artifact must still hash"

        _rows, schemas = flatten(ref)
        assert schemas[leaf["schema_hash"]]["columns"] == [
            "amount", "city", "invoice_no"]

    def test_two_children_differing_only_in_columns_do_not_share_a_schema(self):
        """Hashing on column_types alone would give these one row, and the
        frontend would render one child table's columns for the other."""
        from app.layer1_domain.entities.data_reference import refresh_schema_hash
        root = build_file_ref(file_id="r", columns=["x"], column_types={"x": "s"})
        for cols in (["a", "b"], ["c", "d"]):
            art = add_artifact(root, file_id=f"f{cols[0]}", name=f"n{cols[0]}",
                               artifact_type="nested_array",
                               column_types={}, created_at="2026-01-01T00:00:00+00:00")
            art["columns"] = cols
            refresh_schema_hash(art)
        a, b = root["artifacts"]
        assert a["schema_hash"] != b["schema_hash"]
        assert _roundtrip(root) == root
