"""SA-1910 — the Rust recursive splitter must build the SAME tree as Python.

`_store_rows_recursive` is the only writer of the nested-array CSV tree, and its
readers have been written twice in Python (the control plane's
`_reconstruct_recursive` and the worker SDK's `http_file_ref_resolver`). Moving
the writer into the crate lets both sides share it — but only if the tree it
builds is the one the existing readers already know how to re-join.

So this compares the two writers on the same corpus, on the three things a
reader actually depends on:

* the **shape** — which child tables exist, under which level-relative dotted
  names, at which depth;
* the **row counts** per table, which is what the `__parent_row_id` join fans
  out over;
* the **re-joined value**, reconstructed from the Rust CSVs with the control
  plane's own reader and compared against the Python path's `materialize`.

Where the two legitimately differ, the difference is PINNED here rather than
smoothed over, because a reader that meets an unpinned difference in production
has no way to tell it from corruption.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient

from ._native_streamer import load

_STREAMER = load()

pytestmark = pytest.mark.skipif(
    not hasattr(_STREAMER, "jsonl_split_recursive"),
    reason="native wheel predates jsonl_split_recursive — rebuild it",
)


# ---------------------------------------------------------------------------
# corpus — every shape the split has a rule for
# ---------------------------------------------------------------------------

CORPUS: list[tuple[str, dict]] = [
    ("flat_scalars", {"a": 1, "b": "x", "c": None}),
    ("top_level_table", {"title": "t", "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]}),
    ("table_under_object", {"order": {"id": 1, "lines": [{"sku": "A"}, {"sku": "B"}]}}),
    ("parent_child", {"orders": [{"id": 1, "items": [{"n": 1}, {"n": 2}]}, {"id": 2, "items": [{"n": 3}]}]}),
    ("three_levels", {"a": [{"id": 1, "b": [{"id": 10, "c": [{"v": 100}, {"v": 101}]}]}]}),
    ("mixed_bag", {"name": "x", "count": 5, "meta": {"w": "W1"}, "tags": ["a", "b"],
                   "rows": [{"r": 1}, {"r": 2}]}),
    ("only_a_table", {"items": [{"n": 1}, {"n": 2}]}),
    ("sparse_table", {"rows": [{"a": 1}, {"a": 2}], "other": [{"z": 9}]}),
    ("empty_array_stays_inline", {"e": [], "rows": [{"a": 1}]}),
    ("scalar_array_stays_inline", {"s": [1, 2, 3], "rows": [{"a": 1}]}),
    ("mixed_array_stays_inline", {"m": [{"a": 1}, 7], "rows": [{"a": 1}]}),
    ("two_tables_one_level", {"x": [{"i": 1}], "y": [{"j": 2}]}),
    ("leading_zero_strings", {"rows": [{"zip": "03230", "code": "007"}]}),
    ("deep_object_then_table", {"a": {"b": {"c": {"rows": [{"v": 1}]}}}}),
    ("unicode", {"rows": [{"u": "àèệ — ✓", "k": "ñ"}]}),
]


def _to_jsonl(value: dict | list, path: str) -> None:
    rows = value if isinstance(value, list) else [value]
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False))
            fh.write("\n")


def _rust_tree(value: dict | list) -> dict:
    d = tempfile.mkdtemp(prefix="split-parity-")
    inp = os.path.join(d, "in.jsonl")
    _to_jsonl(value, inp)
    return _STREAMER.jsonl_split_recursive(inp, d)


def _shape_rust(node: dict) -> dict:
    return {
        "name": node["name"],
        "row_count": node["row_count"],
        "children": sorted(
            (_shape_rust(c) for c in node["children"]), key=lambda n: n["name"],
        ),
    }


def _shape_python(ref: dict, name: str = "") -> dict:
    arts = [a for a in (ref.get("artifacts") or [])
            if a.get("artifact_type") == "nested_array"]
    return {
        "name": name,
        "row_count": ref.get("row_count", 0),
        "children": sorted(
            (_shape_python(a, a.get("name", "")) for a in arts),
            key=lambda n: n["name"],
        ),
    }


async def _python_ref(value):
    store = DataStoreService(MockFileServiceClient(), CsvConverter(), recursive_split=True)
    return store, await store.store_data("k", value)


# ---------------------------------------------------------------------------
# 1. same tree
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
async def test_rust_builds_the_same_tree_as_python(name, value):
    _store, ref = await _python_ref(value)
    py = _shape_python(ref)
    rs = _shape_rust(_rust_tree(value))
    assert rs == py, (
        f"{name}: the Rust splitter built a different tree.\n"
        f"python={json.dumps(py, indent=2)}\nrust  ={json.dumps(rs, indent=2)}"
    )


# ---------------------------------------------------------------------------
# 2. the join keys the readers depend on
# ---------------------------------------------------------------------------

def _read_csv(node: dict) -> list[dict]:
    """Read one manifest node's CSV using the types the MANIFEST declares.

    Passing ``None`` here instead was the first thing this suite caught, and it
    is the whole argument for carrying ``column_types`` in the manifest: with no
    declared type ``_try_parse_value`` guesses, and ``"03230"`` comes back as
    ``3230``. The type has to travel with the file, or every reader re-invents
    the same loss.
    """
    with open(node["csv_path"], "rb") as fh:
        rows = CsvConverter().csv_to_dict(
            fh.read(), node.get("column_types"),
            value_kind="array", item_kind="object",
        )
    return rows if isinstance(rows, list) else [rows]


@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
def test_every_child_row_points_at_a_real_parent_row(name, value):
    """`__parent_row_id` must land inside the parent's 0-based `__row_id` range.

    This is the invariant the whole format rests on, and the one a 1-based /
    0-based slip breaks silently: the join still succeeds, it just attaches
    every child to the wrong parent.
    """
    def walk(node):
        parent_rows = _read_csv(node)
        parent_ids = {r.get("__row_id") for r in parent_rows}
        for child in node["children"]:
            for r in _read_csv(child):
                pid = r.get("__parent_row_id")
                assert pid in parent_ids, (
                    f"{name}/{child['name']}: __parent_row_id={pid!r} is not a "
                    f"__row_id of its parent {sorted(parent_ids)!r}"
                )
            walk(child)

    walk(_rust_tree(value))


@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
def test_row_id_is_zero_based_and_dense(name, value):
    def walk(node):
        rows = _read_csv(node)
        ids = [r.get("__row_id") for r in rows]
        assert ids == list(range(len(rows))), f"{name}/{node['name']}: {ids!r}"
        for c in node["children"]:
            walk(c)

    walk(_rust_tree(value))


@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
def test_parent_row_id_is_the_last_column(name, value):
    """Insertion order is column order, and `add_artifact` publishes `columns`
    to the FE. Python appends it last (`c["__parent_row_id"] = idx`)."""
    def walk(node):
        for c in node["children"]:
            with open(c["csv_path"], "r", encoding="utf-8") as fh:
                header = fh.readline().strip().split(",")
            assert header[0] == "__row_id", header
            assert header[-1] == "__parent_row_id", (
                f"{name}/{c['name']}: header={header!r}"
            )
            walk(c)

    walk(_rust_tree(value))


# ---------------------------------------------------------------------------
# 3. the split is lossless — re-join the Rust tree, compare to Python's read
# ---------------------------------------------------------------------------

def _rejoin(node: dict) -> list[dict]:
    """The reader, in the plainest possible form: read this level, attach each
    child list at its dotted path under the parent row it names.

    Deliberately NOT the control plane's `_reconstruct_recursive` — using the
    implementation under comparison to validate itself would prove nothing.
    """
    rows = _read_csv(node)
    for r in rows:
        r.pop("__row_id", None)
        r.pop("__parent_row_id", None)

    parent_rows = _read_csv(node)
    for child in node["children"]:
        joined = _rejoin(child)
        raw_child = _read_csv(child)
        buckets: dict[int, list] = {}
        for raw, clean in zip(raw_child, joined):
            buckets.setdefault(raw.get("__parent_row_id"), []).append(clean)
        for i, parent in enumerate(parent_rows):
            group = buckets.get(parent.get("__row_id"))
            if group is None:
                continue
            cur = rows[i]
            parts = child["name"].split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = group
    return rows


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
async def test_rejoining_the_rust_tree_matches_the_python_read(name, value):
    store, ref = await _python_ref(value)
    expected = await store.materialize(ref)

    rows = _rejoin(_rust_tree(value))
    got = rows[0] if not isinstance(value, list) and rows else rows

    # Canonical dump, not ``==``: 1 == 1.0 and True == 1 in Python, and a type
    # divergence between two CSV writers is exactly what this must catch.
    def canon(x):
        return json.dumps(x, sort_keys=True, default=repr)

    assert canon(got) == canon(expected), (
        f"{name}:\npython={canon(expected)}\nrust  ={canon(got)}"
    )


# ---------------------------------------------------------------------------
# 4. the guards, stated so they are not discovered in production
# ---------------------------------------------------------------------------

def test_a_field_name_cannot_escape_the_output_directory():
    """Field keys come from node output. A local filename derived from one
    would be a path-traversal write; every file the splitter creates is
    sequence-named instead, and the dotted path travels in the manifest."""
    d = tempfile.mkdtemp(prefix="split-escape-")
    inp = os.path.join(d, "in.jsonl")
    with open(inp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"../../pwned": [{"a": 1}], "..\\..\\win": [{"b": 2}]}) + "\n")

    tree = _STREAMER.jsonl_split_recursive(inp, d)

    names = sorted(c["name"] for c in tree["children"])
    assert names == ["../../pwned", "..\\..\\win"], names
    for c in tree["children"]:
        # the NAME is preserved; the PATH is not derived from it
        assert os.path.dirname(os.path.abspath(c["csv_path"])) == os.path.abspath(d)
        assert os.path.basename(c["csv_path"]).startswith("n")


def test_a_non_object_row_is_refused_not_guessed():
    d = tempfile.mkdtemp(prefix="split-bad-")
    inp = os.path.join(d, "in.jsonl")
    with open(inp, "w", encoding="utf-8") as fh:
        fh.write('{"a":1}\n[1,2]\n')
    with pytest.raises(ValueError, match="an array"):
        _STREAMER.jsonl_split_recursive(inp, d)


def test_a_list_root_splits_row_by_row():
    tree = _rust_tree([{"id": 1, "kids": [{"k": 1}]}, {"id": 2, "kids": [{"k": 2}]}])
    assert tree["row_count"] == 2
    assert [c["name"] for c in tree["children"]] == ["kids"]
    assert tree["children"][0]["row_count"] == 2


# ---------------------------------------------------------------------------
# 5. the manifest's own metadata — the fields the callers build a ref from
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
def test_declared_columns_are_exactly_the_csv_header(name, value):
    """`columns` describes ITS OWN file, so it must match that file's header —
    including `__row_id`, which `write_csv_impl` writes but `sketch` never sees.

    Dropping it would be a real regression, not a tidy-up: `_extract_columns`
    flattens an already-stamped row, so the control plane's `columns` carries
    `__row_id` today, and the data factory identifies rows by it.
    """
    def walk(node):
        with open(node["csv_path"], "r", encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
        assert node["columns"] == header, f"{name}/{node['name']}"
        assert "__row_id" in node["columns"]
        for c in node["children"]:
            walk(c)

    walk(_rust_tree(value))


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
async def test_the_same_columns_are_declared_as_python_declares(name, value):
    """As a SET. Order legitimately differs — `write_csv_impl` puts `__row_id`
    first, `dict_to_csv` puts it last — and each file's header matches its own
    declaration, which is the property that actually matters."""
    _store, ref = await _python_ref(value)

    def py_cols(r):
        out = {"": set(r.get("columns") or [])}
        for a in r.get("artifacts") or []:
            if a.get("artifact_type") == "nested_array":
                out.update({a.get("name", ""): set(a.get("columns") or [])}
                           | {f"{a.get('name')}.{k}" if k else a.get("name", ""): v
                              for k, v in py_cols(a).items() if k})
        return out

    def rs_cols(node, prefix=""):
        key = prefix
        out = {key: set(node["columns"])}
        for c in node["children"]:
            out.update(rs_cols(c, c["name"] if not prefix else f"{prefix}.{c['name']}"))
        return out

    py, rs = py_cols(ref), rs_cols(_rust_tree(value))
    assert set(py) == set(rs), f"{name}: different tables"
    for k in py:
        assert py[k] == rs[k], f"{name}/{k}: python={sorted(py[k])} rust={sorted(rs[k])}"


@pytest.mark.parametrize(("name", "value"), CORPUS, ids=[n for n, _ in CORPUS])
def test_size_and_content_key_describe_the_file_on_disk(name, value):
    """Both are carried so an upload can stream from the file. If either is
    computed from something other than the bytes that get uploaded, the file
    service dedupes on a key that does not match its own content."""
    import hashlib

    def walk(node):
        raw = open(node["csv_path"], "rb").read()
        assert node["size_bytes"] == len(raw), f"{name}/{node['name']}"
        assert node["content_key"] == f"sha256:{hashlib.sha256(raw).hexdigest()}"
        for c in node["children"]:
            walk(c)

    walk(_rust_tree(value))


def test_content_key_matches_the_control_planes_own_helper():
    """Same spelling as `_content_key`, or the two producers dedupe apart."""
    from app.layer2_application.data.data_store_service import _content_key

    tree = _rust_tree({"rows": [{"a": 1}]})
    raw = open(tree["csv_path"], "rb").read()
    assert tree["content_key"] == _content_key(raw)
