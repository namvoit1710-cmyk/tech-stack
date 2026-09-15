"""Unit tests for NodeInputEtag."""

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer1_domain.value_objects.node_input_etag import (
    NodeInputEtag,
)


def _tree_three_level() -> dict:
    ref = build_file_ref(file_id="f_main", version=1)
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


def test_compute_is_deterministic():
    tree = _tree_three_level()
    a = NodeInputEtag.of(tree, schema_version="v1")
    b = NodeInputEtag.of(tree, schema_version="v1")
    assert a == b


def test_compute_reflects_descendant_file_id_change():
    tree_before = _tree_three_level()
    tree_after = _tree_three_level()
    # Bump a nested descendant's file_id
    tree_after["artifacts"][0]["artifacts"][0]["file_id"] = "f_li_v2"

    assert NodeInputEtag.of(tree_before) != NodeInputEtag.of(tree_after)


def test_compute_reflects_main_file_id_change():
    a = _tree_three_level()
    b = _tree_three_level()
    b["file_id"] = "f_main_v2"
    assert NodeInputEtag.of(a) != NodeInputEtag.of(b)


def test_compute_reflects_schema_version_change():
    tree = _tree_three_level()
    a = NodeInputEtag.of(tree, schema_version="v1")
    b = NodeInputEtag.of(tree, schema_version="v2")
    assert a != b


def test_compute_reflects_version_counter_bump():
    a = _tree_three_level()
    b = _tree_three_level()
    b["version"] = 2
    assert NodeInputEtag.of(a) != NodeInputEtag.of(b)


def test_compute_order_invariant_for_descendant_discovery():
    """Sibling artifact insertion order shouldn't change the ETag — descendant
    file_ids are sorted before hashing."""
    ref_a = build_file_ref(file_id="f_main")
    add_artifact(ref_a, file_id="f_a", name="a")
    add_artifact(ref_a, file_id="f_b", name="b")

    ref_b = build_file_ref(file_id="f_main")
    add_artifact(ref_b, file_id="f_b", name="b")
    add_artifact(ref_b, file_id="f_a", name="a")

    assert NodeInputEtag.of(ref_a) == NodeInputEtag.of(ref_b)


def test_compute_returns_sentinel_for_non_file_ref():
    etag_none = NodeInputEtag.of(None)
    etag_empty = NodeInputEtag.of({})
    etag_plain = NodeInputEtag.of("not a ref")
    assert etag_none.startswith("sha256:")
    assert etag_none == etag_empty == etag_plain


def test_compute_sentinel_varies_with_schema_version():
    a = NodeInputEtag.of(None, schema_version="v1")
    b = NodeInputEtag.of(None, schema_version="v2")
    assert a != b


def test_as_weak_header_formats_correctly():
    header = NodeInputEtag.as_weak_header("sha256:abc")
    assert header == 'W/"sha256:abc"'


def test_matches_if_match_handles_weak_and_strong_forms():
    etag = "sha256:abc"
    assert NodeInputEtag.matches_if_match('W/"sha256:abc"', etag)
    assert NodeInputEtag.matches_if_match('"sha256:abc"', etag)
    assert NodeInputEtag.matches_if_match('sha256:abc', etag)  # no quotes


def test_matches_if_match_rejects_mismatch_and_empty():
    assert not NodeInputEtag.matches_if_match('W/"sha256:wrong"', "sha256:abc")
    assert not NodeInputEtag.matches_if_match("", "sha256:abc")
    assert not NodeInputEtag.matches_if_match(None, "sha256:abc")
