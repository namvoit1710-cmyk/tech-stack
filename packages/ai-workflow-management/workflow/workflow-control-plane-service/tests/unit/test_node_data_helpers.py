"""Unit specs for ``app.layer2_application.read.node_data_helpers``.

Pure helper functions (no injected collaborators) that build variable-path
dicts, infer type strings, and extract file info from node context values.

Gap-fill: the module sat at 0% unit coverage (sweep 2026-07-02) — only touched
indirectly by an integration endpoint test. Every expectation is grounded in
the source of ``node_data_helpers.py`` + the ``FILE_REF_MARKER`` /
``get_artifacts`` contract in ``layer1_domain.entities.data_reference``.

Case types: happy · edge · invalid input · boundary · failure path.
"""
from __future__ import annotations

from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
from app.layer2_application.read.node_data_helpers import (
    extract_file_id,
    fill_paths_from_preview,
    infer_type,
    meta_paths_for_file_ref,
    paths_from_context,
)


# ── infer_type ────────────────────────────────────────────────────────
# happy + edge across every branch of the source.
def test_infer_type_none_is_string():
    assert infer_type(None) == "string"


def test_infer_type_bool_before_number():
    # bool must be caught before the int/float branch (source order).
    assert infer_type(True) == "boolean"
    assert infer_type(False) == "boolean"


def test_infer_type_int_and_float_are_number():
    assert infer_type(3) == "number"
    assert infer_type(3.14) == "number"


def test_infer_type_dict_and_list():
    assert infer_type({"a": 1}) == "object"
    assert infer_type([1, 2]) == "array"


def test_infer_type_string_true_false_is_boolean():
    assert infer_type("true") == "boolean"
    assert infer_type(" FALSE ") == "boolean"  # stripped + lowered


def test_infer_type_numeric_string_is_number():
    assert infer_type("42") == "number"
    assert infer_type(" -1.5 ") == "number"


def test_infer_type_json_object_and_array_strings():
    assert infer_type('{"k": 1}') == "object"
    assert infer_type("[1, 2, 3]") == "array"


# invalid input / edge: a plain word string falls through to "string".
def test_infer_type_plain_string_default():
    assert infer_type("hello") == "string"


# boundary: empty string strips to "" — not bool, not numeric, not braces.
def test_infer_type_empty_string_is_string():
    assert infer_type("") == "string"


# ── paths_from_context ────────────────────────────────────────────────
# invalid input: non-dict → empty list (source guard).
def test_paths_from_context_non_dict_returns_empty():
    assert paths_from_context("not a dict") == []
    assert paths_from_context(None) == []
    assert paths_from_context([1, 2]) == []


# happy: nested dict yields "top.sub" paths with python type-name + label.
def test_paths_from_context_nested_dict():
    value = {"user": {"name": "Ann", "age": 30}}
    paths = paths_from_context(value)
    by_path = {p["path"]: p for p in paths}
    assert by_path["user.name"]["type"] == "str"
    assert by_path["user.name"]["label"] == "name"
    assert by_path["user.name"]["sample"] == "Ann"
    assert by_path["user.age"]["type"] == "int"


# edge: a scalar top-level value uses the top key as its own path + label.
def test_paths_from_context_scalar_top_level():
    paths = paths_from_context({"count": 5})
    assert paths == [{"path": "count", "type": "int", "label": "count", "sample": 5}]


# happy (file-ref branch): extracted keys become paths sorted, and columns not
# already present are appended.
def test_paths_from_context_file_ref_extracted_and_columns():
    value = {
        FILE_REF_MARKER: True,
        "extracted": {"b.col": {"value": 7}, "a.col": {"value": "x"}},
        "columns": ["a.col", "new_col"],
    }
    paths = paths_from_context(value)
    # extracted iterated sorted → a.col before b.col.
    assert paths[0]["path"] == "a.col"
    assert paths[0]["type"] == "str"          # type of sample "x"
    assert paths[0]["label"] == "col"          # rsplit(".")[-1]
    assert paths[1]["path"] == "b.col"
    assert paths[1]["type"] == "int"
    # "a.col" already extracted → not duplicated; "new_col" appended.
    assert [p["path"] for p in paths] == ["a.col", "b.col", "new_col"]
    new_col = paths[-1]
    assert new_col["type"] == "string"
    assert new_col["sample"] is None


# boundary: file-ref with a non-dict extracted entry → sample None, type string.
def test_paths_from_context_file_ref_non_dict_entry_sample_none():
    value = {FILE_REF_MARKER: True, "extracted": {"k": "scalar-not-dict"}, "columns": []}
    paths = paths_from_context(value)
    assert paths[0]["sample"] is None
    assert paths[0]["type"] == "string"


# ── meta_paths_for_file_ref ───────────────────────────────────────────
# happy: base meta rows always present; artifacts expand into id + by-name rows.
def test_meta_paths_base_rows_and_artifacts():
    value = {
        "file_id": "f1",
        "row_count": 12,
        "content_type": "text/csv",
        "artifacts": [{"name": "detail", "file_id": "af1"}],
        FILE_REF_MARKER: True,
    }
    paths = meta_paths_for_file_ref(value, file_id="explicit")
    by_path = {p["path"]: p for p in paths}
    # explicit file_id wins over value["file_id"].
    assert by_path["__file_id"]["sample"] == "explicit"
    assert by_path["__row_count"]["sample"] == 12
    assert by_path["__content_type"]["sample"] == "text/csv"
    # artifact rows: indexed file_id + by-name.
    assert by_path["__artifacts[0].file_id"]["sample"] == "af1"
    assert by_path["__artifact.detail"]["sample"] == "af1"


# edge: file_id=None falls back to value["file_id"]; a nameless artifact
# yields only the indexed row (no __artifact.<name> row).
def test_meta_paths_fallback_file_id_and_nameless_artifact():
    value = {"file_id": "fallback", "artifacts": [{"file_id": "a0"}], FILE_REF_MARKER: True}
    paths = meta_paths_for_file_ref(value, file_id=None)
    by_path = {p["path"]: p for p in paths}
    assert by_path["__file_id"]["sample"] == "fallback"
    assert "__artifacts[0].file_id" in by_path
    # no name → no by-name path
    assert not any(p["path"].startswith("__artifact.") for p in paths)


# boundary: an empty value dict still emits the 5 base rows, defaults filled.
def test_meta_paths_empty_value_defaults():
    paths = meta_paths_for_file_ref({}, file_id="")
    by_path = {p["path"]: p for p in paths}
    assert by_path["__row_count"]["sample"] == 0
    assert by_path["__content_type"]["sample"] == ""
    assert by_path["__file_id"]["sample"] == ""  # file_id "" → value.get default ""


# ── extract_file_id ───────────────────────────────────────────────────
# invalid input: non-dict → None.
def test_extract_file_id_non_dict_none():
    assert extract_file_id("x") is None
    assert extract_file_id(None) is None


# happy: file-ref marker → coerced str file_id.
def test_extract_file_id_from_file_ref():
    assert extract_file_id({FILE_REF_MARKER: True, "file_id": 123}) == "123"


# edge: file-ref marker but empty file_id → None (falsy guard).
def test_extract_file_id_file_ref_empty_none():
    assert extract_file_id({FILE_REF_MARKER: True, "file_id": ""}) is None


# happy: non-file-ref with output.uri → last URI segment.
def test_extract_file_id_from_output_uri():
    value = {"output": {"uri": "https://store/bucket/objects/abc123/"}}
    assert extract_file_id(value) == "abc123"


# failure path / edge: plain dict with no marker + no output → None.
def test_extract_file_id_plain_dict_none():
    assert extract_file_id({"foo": "bar"}) is None


# ── fill_paths_from_preview ───────────────────────────────────────────
# boundary: empty rows → no mutation (early return).
def test_fill_paths_from_preview_empty_rows_noop():
    paths: list[dict] = []
    fill_paths_from_preview(paths, [])
    assert paths == []


# happy: new columns from the first row are appended with inferred types;
# "_row_index" is skipped and pre-existing paths are not duplicated.
def test_fill_paths_from_preview_adds_new_columns():
    paths = [{"path": "existing", "type": "string", "label": "existing", "sample": None}]
    rows = [{"existing": "v", "amount": "12", "flag": "true", "_row_index": 0}]
    fill_paths_from_preview(paths, rows)
    by_path = {p["path"]: p for p in paths}
    # existing untouched, _row_index skipped
    assert "_row_index" not in by_path
    assert len(paths) == 3  # existing + amount + flag
    assert by_path["amount"]["type"] == "number"   # infer_type("12")
    assert by_path["flag"]["type"] == "boolean"     # infer_type("true")
    assert by_path["amount"]["label"] == "amount"
