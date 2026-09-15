"""SA-1078 — Array Value Transform engine + multi-type node wiring.

Covers the new two-array engine (match/mode/unmatched/extra/overrides/status) and
asserts the multi-type wiring keeps the existing mapping node byte-for-byte
(``_mapping_handler`` runs the unchanged ExecuteTaskUseCase; MAPPING_NODE_DEF
preserves worker_type + schema).
"""
from __future__ import annotations

import json
import pytest

from app.layer4_frameworks.providers.array_transform_engine import ArrayTransformEngine
from app.node_types import (
    _array_transform_handler, _mapping_handler,
    MAPPING_NODE_DEF, ARRAY_TRANSFORM_NODE_DEF, NODE_TYPES,
    ARRAY_TRANSFORM_INPUT_SCHEMA, ARRAY_TRANSFORM_OUTPUT_SCHEMA,
)
from app.layer2_application.features.execute_task.use_cases.execute_task_usecase import (
    MAPPING_INPUT_SCHEMA, MAPPING_OUTPUT_SCHEMA,
)


def _val(result, field):
    return next(r["value"] for r in result if r["field"] == field)


def _status(changes, field):
    return next(c["status"] for c in changes if c["field"] == field)


# --- Engine: modes ---

def test_replace_overwrites_on_match():
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": 1}], [{"field": "a", "value": 2}], mode="replace")
    assert _val(out["result"], "a") == 2
    assert _status(out["changes"], "a") == "replaced"


def test_replace_equal_is_unchanged():
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": 1}], [{"field": "a", "value": 1}], mode="replace")
    assert _status(out["changes"], "a") == "unchanged"
    assert out["summary"]["unchanged"] == 1


def test_enrich_keeps_both():
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": "old"}], [{"field": "a", "value": "new"}], mode="enrich")
    assert _val(out["result"], "a") == {"original": "old", "enriched": "new"}
    assert _status(out["changes"], "a") == "enriched"


def test_merge_dicts_deep():
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": {"x": 1, "n": {"p": 1}}}],
        [{"field": "a", "value": {"y": 2, "n": {"q": 2}}}], mode="merge")
    assert _val(out["result"], "a") == {"x": 1, "y": 2, "n": {"p": 1, "q": 2}}
    assert _status(out["changes"], "a") == "merged"


def test_merge_lists_concat():
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": [1]}], [{"field": "a", "value": [2]}], mode="merge")
    assert _val(out["result"], "a") == [1, 2]


# --- Engine: match_by ---

def test_match_by_value():
    out = ArrayTransformEngine().transform(
        [{"field": "x", "value": "v1"}],
        [{"field": "y", "value": "v1"}],  # different field, same value
        match_by="value", mode="replace")
    # matched on value → array2's value (same) → unchanged
    assert out["summary"]["matched"] == 1


# --- Engine: unmatched / extra ---

def test_unmatched_keep_and_drop():
    a1 = [{"field": "only1", "value": 9}]
    a2 = [{"field": "other", "value": 1}]
    keep = ArrayTransformEngine().transform(a1, a2, unmatched="keep", extra="ignore")
    assert _val(keep["result"], "only1") == 9
    assert _status(keep["changes"], "only1") == "unmatched"
    drop = ArrayTransformEngine().transform(a1, a2, unmatched="drop", extra="ignore")
    assert all(r["field"] != "only1" for r in drop["result"])


def test_extra_ignore_and_add():
    a1 = [{"field": "a", "value": 1}]
    a2 = [{"field": "a", "value": 1}, {"field": "z", "value": 99}]
    ign = ArrayTransformEngine().transform(a1, a2, extra="ignore")
    assert all(r["field"] != "z" for r in ign["result"])
    assert _status(ign["changes"], "z") == "extra"
    assert ign["summary"]["extra"] == 1
    add = ArrayTransformEngine().transform(a1, a2, extra="add")
    assert _val(add["result"], "z") == 99


def test_field_override_mode():
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": "o"}, {"field": "b", "value": "o"}],
        [{"field": "a", "value": "n"}, {"field": "b", "value": "n"}],
        mode="replace", field_overrides={"b": "enrich"})
    assert _val(out["result"], "a") == "n"                                  # global replace
    assert _val(out["result"], "b") == {"original": "o", "enriched": "n"}   # override enrich


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        ArrayTransformEngine().transform([], [], mode="nope")
    with pytest.raises(ValueError):
        ArrayTransformEngine().transform([], [], match_by="nope")


# --- AC Scenario 6: output tracks old|new|status for EACH field ---

def test_scenario6_changes_track_old_new_status_for_every_field():
    """SA-1078 AC S6: the preview data must include old value, new value, and
    status for EACH field — matched (replaced/unchanged), unmatched (array1
    only) and extra (array2 only) alike."""
    out = ArrayTransformEngine().transform(
        [{"field": "a", "value": 1}, {"field": "b", "value": 2}, {"field": "only1", "value": 9}],
        [{"field": "a", "value": 10}, {"field": "b", "value": 2}, {"field": "z", "value": 99}],
        mode="replace", unmatched="keep", extra="add")
    by = {c["field"]: c for c in out["changes"]}
    for f in ("a", "b", "only1", "z"):
        assert f in by, f"field {f} missing from changes"
        assert {"old", "new", "status"} <= set(by[f].keys())
    assert by["a"]["status"] == "replaced"
    assert by["b"]["status"] == "unchanged"
    assert by["only1"]["status"] == "unmatched"
    assert by["z"]["status"] == "extra"


# --- Handler ---

def test_array_transform_handler_accepts_list_and_json_string():
    a1 = [{"field": "a", "value": 1}]
    a2 = [{"field": "a", "value": 2}]
    out1 = _array_transform_handler({"array1": a1, "array2": a2, "mode": "replace"}, {})
    assert _val(out1["result"], "a") == 2
    out2 = _array_transform_handler({"array1": json.dumps(a1), "array2": json.dumps(a2), "mode": "replace"}, {})
    assert out1 == out2


def test_array_transform_handler_field_overrides_from_inputs():
    out = _array_transform_handler({
        "array1": [{"field": "a", "value": "o"}],
        "array2": [{"field": "a", "value": "n"}],
        "mode": "replace",
        "field_overrides": [{"field": "a", "mode": "enrich"}],
    }, {})
    assert _val(out["result"], "a") == {"original": "o", "enriched": "n"}


# --- Mapping node PARITY (must be unchanged) ---

def test_mapping_handler_still_works():
    out = _mapping_handler({
        "source_data": {"name": "Alice", "age": "30"},
        "mapping_rules": [
            {"source_path": "name", "target_field": "full_name", "field_type": "string"},
            {"source_path": "age", "target_field": "age", "field_type": "number"},
        ],
    }, {})
    assert out["mapped_data"] == {"full_name": "Alice", "age": 30}
    assert out["applied_rules_count"] == 2


def test_mapping_handler_raises_on_missing_source_data():
    with pytest.raises(Exception):
        _mapping_handler({}, {})


# --- Multi-type registration parity ---

def test_node_defs_registered():
    assert {nt.worker_type for nt in NODE_TYPES} == {"mapping_data", "array_transform", "mapping_iterate", "mapping_aggregate"}


def test_mapping_node_def_preserves_contract():
    assert MAPPING_NODE_DEF.worker_type == "mapping_data"
    assert MAPPING_NODE_DEF.input_schema == MAPPING_INPUT_SCHEMA
    assert MAPPING_NODE_DEF.output_schema == MAPPING_OUTPUT_SCHEMA
    assert MAPPING_NODE_DEF.handler is not None


def test_array_transform_node_def_shape():
    assert ARRAY_TRANSFORM_NODE_DEF.worker_type == "array_transform"
    assert ARRAY_TRANSFORM_NODE_DEF.input_schema == ARRAY_TRANSFORM_INPUT_SCHEMA
    assert ARRAY_TRANSFORM_NODE_DEF.output_schema == ARRAY_TRANSFORM_OUTPUT_SCHEMA
    keys = {f["key"] for f in ARRAY_TRANSFORM_INPUT_SCHEMA}
    assert {"array1", "array2", "match_by", "mode", "unmatched", "extra", "field_overrides"} <= keys
