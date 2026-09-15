"""SA-2072 — worker-side full-file_ref reconstruction (parity with the control
plane's ``DataStoreService.materialize`` / ``_reconstruct_recursive``).

The control plane, when it claim-checks a large TASK input, now emits the FULL
``file_ref`` (artifact tree + ``value_kind``) instead of the lossy
``{file_id, data_mode:"csv_single"}`` pointer. This module proves the worker
rebuilds the ORIGINAL nested object from that tree.

Two independent checks guard against a self-consistent-but-wrong reader:

1. **Round-trip** — a compact in-test writer that mirrors the CP's
   ``_store_rows_recursive`` (prune arrays-of-dicts → child CSVs keyed by
   ``__parent_row_id``; scalars flattened to dotted, CSV-STRING cells; kinds +
   ``column_types`` declared). ``materialize == original`` for objects/arrays,
   1-row array children (SA-1977), sparse arrays, deep nesting and the flat
   ``array_field`` split.
2. **Hand-authored fixture** — literal OData rows + ref (no shared writer) for the
   exact reported MDG shape, asserting the exact reconstructed object.

Plus the invariants the fix must not break: the ``csv_single`` path and an
artifact-less / kind-less ref are byte-identical to today; the SA-1943 input cap
stays cumulative across the whole tree.
"""
import json
from unittest.mock import MagicMock

import pytest

from worker_sdk.layer1_domain.exceptions import FileRefResolutionError
from worker_sdk.layer4_frameworks.providers.file_service.http_file_ref_resolver import (
    HttpFileRefResolver,
)


# ---------------------------------------------------------------------------
# Fake OData file service: file_id -> list[flat string-cell rows]
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, rows):
        self._payload = {"data": {"data": rows}}
        self.content = json.dumps(self._payload).encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """Routes ``GET .../odata/{file_id}/data`` to the store; one short page each
    (all test collections are far under the page size)."""

    def __init__(self, store):
        self._store = store
        self.calls = []

    def get(self, url, params=None):
        file_id = url.split("/odata/")[1].split("/data")[0]
        self.calls.append((file_id, params))
        # $skip past the (single) page returns empty → paging terminates.
        skip = int((params or {}).get("$skip", "0"))
        rows = self._store.get(file_id, [])
        return _FakeResponse(rows if skip == 0 else [])


def _resolver(store, **kw):
    r = HttpFileRefResolver("http://fs:8000", **kw)
    r._client = _FakeClient(store)
    return r


# ---------------------------------------------------------------------------
# Compact writer mirroring DataStoreService._store_rows_recursive (SA-1910/1977)
# ---------------------------------------------------------------------------

def _flatten_typed(d, prefix=""):
    out = {}
    for k, v in d.items():
        key = k if not prefix else f"{prefix}.{k}"
        if isinstance(v, dict):
            out.update(_flatten_typed(v, key))
        else:
            out[key] = v  # scalar / None / non-pruned list — typed
    return out


def _stringify(flat):
    """Turn a typed flat row into CSV-STRING cells, as the file service serves."""
    out = {}
    for k, v in flat.items():
        if v is None:
            out[k] = ""
        elif isinstance(v, bool):
            out[k] = "true" if v else "false"
        elif isinstance(v, (list, dict)):
            out[k] = json.dumps(v)
        else:
            out[k] = str(v)
    return out


def _infer_types(rows):
    types = {}
    for r in rows:
        for k, v in _flatten_typed(r).items():
            if k in types or v is None:
                continue
            if isinstance(v, bool):
                types[k] = "boolean"
            elif isinstance(v, (int, float)):
                types[k] = "number"
            elif isinstance(v, str):
                types[k] = "string"
            elif isinstance(v, (list, dict)):
                types[k] = "json"
    return types


def _prune(row):
    """Mirror CsvConverter.prune_arrays_of_dicts: pull out arrays whose elements
    are ALL dicts; recurse into nested objects; everything else stays inline."""
    pruned, arrays = {}, {}

    def walk(src, dst, prefix):
        for k, v in src.items():
            path = k if not prefix else f"{prefix}.{k}"
            if isinstance(v, list) and len(v) > 0 and all(isinstance(x, dict) for x in v):
                arrays[path] = v
            elif isinstance(v, dict):
                sub = {}
                walk(v, sub, path)
                dst[k] = sub
            else:
                dst[k] = v

    walk(row, pruned, "")
    return pruned, arrays


def _store_recursive(store, key, rows, as_list, artifact_type="nested_array"):
    pruned_rows, per_path = [], {}
    for idx, row in enumerate(rows):
        pruned, arrays = _prune(row)
        pruned["__row_id"] = idx
        pruned_rows.append(pruned)
        for path, arr in arrays.items():
            bucket = per_path.setdefault(path, [])
            for child in arr:
                c = dict(child)
                c["__parent_row_id"] = idx
                bucket.append(c)

    payload_rows = pruned_rows if as_list else [pruned_rows[0] if pruned_rows else {}]
    col_types = _infer_types(payload_rows)
    file_id = f"file-{key}"
    store[file_id] = [_stringify(_flatten_typed(r)) for r in payload_rows]

    ref = {
        "__file_ref": True, "file_id": file_id, "content_type": "text/csv",
        "row_count": len(payload_rows), "column_types": col_types,
        "value_kind": "array" if as_list else "object",
        "item_kind": "object" if as_list else "", "artifacts": [],
    }
    for path, child_rows in per_path.items():
        child_ref = _store_recursive(store, f"{key}-{path}", child_rows, as_list=True)
        ref["artifacts"].append({
            "file_id": child_ref["file_id"], "name": path,
            "artifact_type": "nested_array", "parent_row_key_column": "__parent_row_id",
            "column_types": child_ref["column_types"], "row_count": child_ref["row_count"],
            "value_kind": "array", "item_kind": "object",
            "artifacts": child_ref["artifacts"],
        })
    return ref


def _build(store, data):
    return _store_recursive(store, "root", data if isinstance(data, list) else [data],
                            as_list=isinstance(data, list))


def _materialize(data):
    """Round-trip *data* through the mirror-writer and the worker reader."""
    store = {}
    ref = _build(store, data)
    return _resolver(store).resolve_inputs(ref)


# ---------------------------------------------------------------------------
# Round-trip parity
# ---------------------------------------------------------------------------

def test_object_root_with_nested_arrays():
    original = {
        "url": "https://api/convert", "method": "POST", "body_type": "raw_json",
        "headers": [{"header_name": "Content-Type", "header_value": "application/json"}],
        "body": [
            {"sku": "A1", "qty": 3, "active": True},
            {"sku": "B2", "qty": 0, "active": False},
        ],
    }
    assert _materialize(original) == original


def test_single_row_array_child_stays_a_list():
    # SA-1977 — a recursively-split list of exactly ONE row must come back a LIST,
    # not collapse to its single dict.
    original = {"items": [{"a": 1}]}
    out = _materialize(original)
    assert out == original
    assert isinstance(out["items"], list) and len(out["items"]) == 1


def test_deep_three_level_nesting():
    original = {
        "payload": [
            {
                "deepData": {"product": "P1", "isDelete": False},
                "to_Classification": [
                    {"classType": "001", "to_Characteristic": [
                        {"charc": "COLOR", "counter": 1},
                        {"charc": "SIZE", "counter": 2},
                    ]},
                ],
                "to_Description": [
                    {"language": "EN", "productDescription": "widget"},
                    {"language": "DE", "productDescription": "widget-de"},
                ],
            }
        ]
    }
    assert _materialize(original) == original


def test_two_parents_children_grouped_by_parent_row():
    original = {
        "orders": [
            {"id": 10, "lines": [{"n": "a"}, {"n": "b"}]},
            {"id": 20, "lines": [{"n": "c"}]},
        ]
    }
    out = _materialize(original)
    assert out == original
    assert [l["n"] for l in out["orders"][0]["lines"]] == ["a", "b"]
    assert [l["n"] for l in out["orders"][1]["lines"]] == ["c"]


def test_sparse_and_scalar_arrays_ride_inline():
    # A heterogeneous array (a None among dicts) and a scalar array are NOT split
    # (kept inline as JSON cells) — they must still round-trip verbatim.
    original = {
        "records": [{"id": 1}, None, {"id": 3}],
        "tags": ["x", "y", "z"],
        "name": "batch-1",
    }
    assert _materialize(original) == original


def test_list_root_of_objects():
    original = [{"a": 1, "kids": [{"k": "x"}]}, {"a": 2, "kids": [{"k": "y"}]}]
    assert _materialize(original) == original


def test_array_field_flat_split_reattaches_whole():
    # Flag-OFF writer path: a single-object root with one array_field artifact.
    store = {}
    store["file-root"] = [{"name": "job-1", "__row_id": "0"}]
    store["file-items"] = [
        {"sku": "A1", "qty": "3", "__parent_row_id": "0", "__row_id": "0"},
        {"sku": "B2", "qty": "5", "__parent_row_id": "0", "__row_id": "1"},
    ]
    ref = {
        "__file_ref": True, "file_id": "file-root", "value_kind": "object",
        "column_types": {"name": "string"},
        "artifacts": [{
            "file_id": "file-items", "name": "items", "artifact_type": "array_field",
            "value_kind": "array", "item_kind": "object",
            "column_types": {"sku": "string", "qty": "number"},
        }],
    }
    out = _resolver(store).resolve_inputs(ref)
    assert out == {"name": "job-1", "items": [{"sku": "A1", "qty": 3}, {"sku": "B2", "qty": 5}]}


# ---------------------------------------------------------------------------
# Hand-authored fixture — independent of the mirror-writer
# ---------------------------------------------------------------------------

def test_handauthored_mdg_shape_exact():
    """Literal OData rows + ref for the reported shape: an object whose only field
    ``payload`` is a 1-item array carrying a nested ``to_Description`` array. No
    shared writer — the expected object is asserted exactly."""
    store = {
        # root: only-arrays object → one synthetic row with just __row_id
        "f-root": [{"__row_id": "0"}],
        # payload[0]
        "f-payload": [
            {"deepData.product": "MAT-1", "deepData.itemID": "7",
             "__parent_row_id": "0", "__row_id": "0"},
        ],
        # payload[0].to_Description[*]
        "f-desc": [
            {"language": "EN", "productDescription": "steel plate",
             "__parent_row_id": "0", "__row_id": "0"},
            {"language": "DE", "productDescription": "stahlplatte",
             "__parent_row_id": "0", "__row_id": "1"},
        ],
    }
    ref = {
        "__file_ref": True, "file_id": "f-root", "value_kind": "object",
        "item_kind": "", "column_types": {}, "artifacts": [{
            "file_id": "f-payload", "name": "payload", "artifact_type": "nested_array",
            "parent_row_key_column": "__parent_row_id", "value_kind": "array",
            "item_kind": "object",
            "column_types": {"deepData.product": "string", "deepData.itemID": "number"},
            "artifacts": [{
                "file_id": "f-desc", "name": "to_Description",
                "artifact_type": "nested_array", "parent_row_key_column": "__parent_row_id",
                "value_kind": "array", "item_kind": "object",
                "column_types": {"language": "string", "productDescription": "string"},
                "artifacts": [],
            }],
        }],
    }
    out = _resolver(store).resolve_inputs(ref)
    assert out == {
        "payload": [{
            "deepData": {"product": "MAT-1", "itemID": 7},
            "to_Description": [
                {"language": "EN", "productDescription": "steel plate"},
                {"language": "DE", "productDescription": "stahlplatte"},
            ],
        }]
    }


# ---------------------------------------------------------------------------
# Invariants the fix must NOT break
# ---------------------------------------------------------------------------

def test_backward_compat_csv_single_fallback_still_reconstructs():
    # SA-2072 backward-compat — the control plane tags the full ref with a
    # ``data_mode:"csv_single"`` fallback so OLD workers still read it. A NEW
    # worker must reconstruct in full (reconstructable-first beats the fallback),
    # NOT take the lossy single-file path the fallback would otherwise trigger.
    store = {}
    original = {"url": "u", "method": "POST", "body": [{"sku": "A1", "qty": 3}]}
    ref = _build(store, original)
    ref["data_mode"] = "csv_single"  # exactly what node_execution_runner now emits
    out = _resolver(store).resolve_inputs(ref)
    assert out == original
    assert isinstance(out["body"], list)  # NOT collapsed to the root row


def test_csv_single_pointer_unchanged():
    # The intentional data_mode feature path stays byte-identical: first row → dict.
    store = {"f-1": [{"name": "John", "__row_id": "0"}]}
    r = _resolver(store)
    out = r.resolve_inputs({"file_id": "f-1", "data_mode": "csv_single"})
    assert out == {"name": "John", "__row_id": "0"}  # csv_single path does not unflatten/strip


def test_artifactless_kindless_ref_returns_list_unchanged():
    # The existing nested-ref contract (no artifacts, no value_kind → flat rows).
    store = {"f-9": [{"name": "Alice"}, {"name": "Bob"}]}
    r = _resolver(store)
    out = r.resolve_inputs({"source_data": {"__file_ref": True, "file_id": "f-9", "row_count": 2},
                            "mode": "strict"})
    assert out["mode"] == "strict"
    assert out["source_data"] == [{"name": "Alice"}, {"name": "Bob"}]


def test_object_kind_no_artifacts_returns_dict():
    # A large flat object (no array fields) offloaded: value_kind=object, no
    # artifacts → the object, matching the old csv_single result (not a list).
    store = {"f-o": [{"a": "1", "b": "hi", "__row_id": "0"}]}
    ref = {"__file_ref": True, "file_id": "f-o", "value_kind": "object",
           "column_types": {"a": "number", "b": "string"}, "artifacts": []}
    out = _resolver(store).resolve_inputs(ref)
    assert out == {"a": 1, "b": "hi"}


def test_input_cap_is_cumulative_across_the_tree():
    # Root (~540 B) and child (~560 B) are EACH under the 800 B cap, but their SUM
    # (~1.1 KB) exceeds it → raise (C4). A per-file cap would let this slip through;
    # the cumulative budget across the whole tree is what catches it.
    store = {}
    ref = _build(store, {"note": "R" * 500, "body": [{"blob": "B" * 500}]})
    root_bytes = _FakeResponse(store[ref["file_id"]]).content
    child_bytes = _FakeResponse(store[ref["artifacts"][0]["file_id"]]).content
    assert len(root_bytes) < 800 and len(child_bytes) < 800          # each under the cap
    assert len(root_bytes) + len(child_bytes) > 800                  # sum over the cap
    r = _resolver(store, max_input_bytes=800)
    with pytest.raises(FileRefResolutionError, match="OOM guard|worker input cap"):
        r.resolve_inputs(ref)


def test_no_regression_on_plain_inputs():
    r = _resolver({})
    assert r.resolve_inputs({"url": "https://x", "method": "GET"}) == {"url": "https://x", "method": "GET"}
