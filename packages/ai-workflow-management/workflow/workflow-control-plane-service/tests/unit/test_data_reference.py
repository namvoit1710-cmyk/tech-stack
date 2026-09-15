"""Unit tests for the recursive file-reference helpers in
``app.layer1_domain.entities.data_reference``.

Covers Unit A (Phase 1 Foundation) additions:

- ``build_file_ref`` returns the expected shape including ``version``, ``etag``,
  ``artifact_id``, and a recursive ``artifacts`` list.
- ``walk_file_ref`` does DFS over a 3-level tree.
- ``find_artifact`` looks up an artifact by dotted-path id; returns ``None`` on
  miss.
- ``register_artifacts`` stores metadata objects (not bare file_ids) and
  propagates ``node_id`` / ``task_id`` as provenance.
- Missing ``name`` or ``file_id`` entries are skipped (legacy contract
  preserved).
"""

import pytest

from app.layer1_domain.entities.data_reference import (
    FILE_REF_MARKER,
    ROOT_ARTIFACT_ID,
    add_artifact,
    build_file_ref,
    find_artifact,
    register_artifacts,
    walk_file_ref,
)


# ---------------------------------------------------------------------------
# build_file_ref
# ---------------------------------------------------------------------------

def test_build_file_ref_returns_marker_and_defaults():
    ref = build_file_ref(file_id="f_root")
    assert ref[FILE_REF_MARKER] is True
    assert ref["file_id"] == "f_root"
    assert ref["artifact_id"] == ROOT_ARTIFACT_ID
    assert ref["artifacts"] == []
    assert ref["version"] == 0
    assert ref["etag"] == ""
    assert ref["columns"] == []
    # No-``extracted`` model: file_refs are pointer-only, no extracted key.
    assert "extracted" not in ref


def test_build_file_ref_accepts_version_etag_and_artifact_id():
    ref = build_file_ref(
        file_id="f_root",
        version=7,
        etag="sha256:abc",
        artifact_id="__root__",
        columns=["a", "b"],
    )
    assert ref["version"] == 7
    assert ref["etag"] == "sha256:abc"
    assert ref["artifact_id"] == "__root__"
    assert ref["columns"] == ["a", "b"]


def test_add_artifact_includes_recursive_artifacts_list_and_parent_col():
    ref = build_file_ref(file_id="f_root")
    art = add_artifact(
        ref,
        file_id="f_orders",
        name="orders",
        artifact_id="orders",
        artifact_type="array_field",
    )
    assert art["artifact_id"] == "orders"
    assert art["artifacts"] == []  # recursive container present
    assert art["parent_row_key_column"] is None
    assert ref["artifacts"] == [art]

    nested = add_artifact(
        art,
        file_id="f_line_items",
        name="line_items",
        artifact_id="orders.line_items",
        artifact_type="nested_array_field",
        parent_row_key_column="__parent_row_id",
    )
    assert nested["parent_row_key_column"] == "__parent_row_id"
    assert art["artifacts"] == [nested]


def test_add_artifact_defaults_artifact_id_to_name():
    ref = build_file_ref(file_id="f_root")
    art = add_artifact(ref, file_id="f_orders", name="orders")
    assert art["artifact_id"] == "orders"


# ---------------------------------------------------------------------------
# walk_file_ref
# ---------------------------------------------------------------------------

def _make_three_level_tree() -> dict:
    ref = build_file_ref(file_id="f_main")
    orders = add_artifact(ref, file_id="f_orders", name="orders", artifact_id="orders")
    line_items = add_artifact(
        orders, file_id="f_li", name="line_items",
        artifact_id="orders.line_items",
        parent_row_key_column="__parent_row_id",
    )
    add_artifact(
        line_items, file_id="f_attrs", name="attrs",
        artifact_id="orders.line_items.attrs",
        parent_row_key_column="__parent_row_id",
    )
    return ref


def test_walk_file_ref_visits_all_levels_depth_first():
    tree = _make_three_level_tree()
    visited: list[tuple[str, int]] = []

    def visitor(node, depth):
        visited.append((node.get("artifact_id", ""), depth))

    walk_file_ref(tree, visitor)
    assert visited == [
        (ROOT_ARTIFACT_ID, 0),
        ("orders", 1),
        ("orders.line_items", 2),
        ("orders.line_items.attrs", 3),
    ]


def test_walk_file_ref_no_op_on_non_ref():
    calls = []
    walk_file_ref({"not": "a file ref"}, lambda n, d: calls.append(n))
    walk_file_ref(None, lambda n, d: calls.append(n))
    walk_file_ref("string", lambda n, d: calls.append(n))
    assert calls == []


# ---------------------------------------------------------------------------
# find_artifact
# ---------------------------------------------------------------------------

def test_find_artifact_matches_root_by_default_id():
    tree = _make_three_level_tree()
    assert find_artifact(tree, ROOT_ARTIFACT_ID) is tree


def test_find_artifact_matches_nested_by_dotted_path():
    tree = _make_three_level_tree()
    li = find_artifact(tree, "orders.line_items")
    assert li is not None
    assert li["file_id"] == "f_li"

    deepest = find_artifact(tree, "orders.line_items.attrs")
    assert deepest is not None
    assert deepest["file_id"] == "f_attrs"


def test_find_artifact_returns_none_on_miss():
    tree = _make_three_level_tree()
    assert find_artifact(tree, "orders.does_not_exist") is None
    assert find_artifact(tree, "") is None


def test_find_artifact_handles_non_file_ref_input():
    assert find_artifact(None, "x") is None
    assert find_artifact({"not": "a ref"}, "x") is None


# ---------------------------------------------------------------------------
# register_artifacts (metadata-object shape)
# ---------------------------------------------------------------------------

def _file_ref_with_named_artifacts() -> dict:
    ref = build_file_ref(file_id="f_main")
    add_artifact(
        ref,
        file_id="f_invoice",
        name="invoice_pdf",
        content_type="application/pdf",
        size_bytes=284112,
        artifact_type="upload",
    )
    add_artifact(
        ref,
        file_id="f_receipts",
        name="receipts_csv",
        content_type="text/csv",
        size_bytes=1024,
        artifact_type="data",
    )
    return ref


def test_register_artifacts_stores_metadata_object_per_name():
    variables: dict = {}
    ref = _file_ref_with_named_artifacts()

    register_artifacts(
        variables,
        ref,
        node_id="upload_invoice",
        task_id="t_1",
    )

    assert set(variables["artifacts"].keys()) == {"invoice_pdf", "receipts_csv"}
    invoice = variables["artifacts"]["invoice_pdf"]
    assert isinstance(invoice, dict)
    assert invoice["file_id"] == "f_invoice"
    assert invoice["content_type"] == "application/pdf"
    assert invoice["size_bytes"] == 284112
    assert invoice["produced_by_node"] == "upload_invoice"
    assert invoice["produced_by_task"] == "t_1"
    assert isinstance(invoice["registered_at"], str) and invoice["registered_at"]


def test_register_artifacts_provenance_defaults_empty_when_not_provided():
    variables: dict = {}
    ref = _file_ref_with_named_artifacts()

    register_artifacts(variables, ref)

    entry = variables["artifacts"]["invoice_pdf"]
    assert entry["produced_by_node"] == ""
    assert entry["produced_by_task"] == ""


def test_register_artifacts_skips_entries_missing_name_or_file_id():
    variables: dict = {}
    ref = build_file_ref(file_id="f_main")
    # Named, but no file_id → skipped.
    add_artifact(ref, file_id="", name="no_file", artifact_type="data")
    # Has file_id but no name → skipped.
    add_artifact(ref, file_id="f_unnamed", name="", artifact_type="data")
    # Valid.
    add_artifact(ref, file_id="f_ok", name="ok_key", artifact_type="data")

    register_artifacts(variables, ref)

    assert list(variables["artifacts"].keys()) == ["ok_key"]


def test_register_artifacts_no_op_on_non_file_ref():
    variables: dict = {}
    register_artifacts(variables, None)
    register_artifacts(variables, {"not": "a ref"})
    register_artifacts(variables, "string")
    assert "artifacts" not in variables


def test_register_artifacts_merges_into_existing_registry():
    variables: dict = {"artifacts": {"old_key": {"file_id": "f_old"}}}
    ref = _file_ref_with_named_artifacts()

    register_artifacts(variables, ref, node_id="n", task_id="t")

    assert "old_key" in variables["artifacts"]
    assert "invoice_pdf" in variables["artifacts"]
    assert "receipts_csv" in variables["artifacts"]


def test_register_artifacts_overwrites_entry_with_same_name():
    variables: dict = {
        "artifacts": {"invoice_pdf": {"file_id": "f_old", "produced_by_node": "old_node"}}
    }
    ref = _file_ref_with_named_artifacts()

    register_artifacts(variables, ref, node_id="new_node", task_id="t_new")

    entry = variables["artifacts"]["invoice_pdf"]
    assert entry["file_id"] == "f_invoice"
    assert entry["produced_by_node"] == "new_node"
    assert entry["produced_by_task"] == "t_new"
