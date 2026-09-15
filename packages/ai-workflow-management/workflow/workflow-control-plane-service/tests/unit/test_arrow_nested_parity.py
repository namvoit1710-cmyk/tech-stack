"""SA-1905 — what the nested Arrow sidecar reads back, versus what the CSV does.

The sidecar exists because CSV cannot express what the data is: nesting becomes
a tree of files joined on ``__parent_row_id``; a missing field and an empty
string are the same bytes (SA-1988); a leaf column and a branch column at one
path have to be arbitrated per row from cell emptiness (SA-1984); and the type
lives in a ``column_types`` map that can go missing, at which point ``"0123"``
becomes ``123``.

Arrow expresses all of that natively. So the two readings **cannot** be equal
everywhere, and a test asserting they are would be asserting the bugs. Every
case is therefore filed into one of four buckets and **pinned**:

===========================  ==================================================
``AGREE``                    ``arrow == csv == original``
``ARROW_BETTER``             ``arrow == original``, ``csv`` is pinned lossy
``BOTH_WIDEN``               ``arrow == csv``, both pinned — a shared property
``ARROW_BETTER_NOT_EXACT``   both pinned; Arrow is closer but still not exact
===========================  ==================================================

The last bucket is the honest one. Arrow's ``Struct`` has a validity bit per
VALUE, not a "this field was absent on this row" bit, so a sparse record still
comes back with the key present and null. That is not fixed here and is not
claimed to be — fixing it needs ``Map`` instead of ``Struct``, which throws the
schema away.

**Every bucket was MEASURED before it was pinned.** Writing this file from
predictions would have filed ``[{"id":1}, null, {"id":3}]`` under
``ARROW_BETTER`` and certified a real defect: a null LINE was becoming a row of
nulls, which the CSV path gets right. See
``a_null_line_forces_the_reserved_column`` in the crate for the fix.

**The comparator is a canonical JSON dump, never ``==``.** In Python ``1 == 1.0``
and ``True == 1``, so an equality assertion passes for exactly the type
divergences this file is looking for.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

from tests.unit._native_streamer import load as _load_native

jcs = _load_native()

from app.layer1_domain.value_objects.value_kind import ARRAY
from app.layer2_application.data.csv_converter import CsvConverter

ROW_ID = jcs.ROW_ID_COLUMN


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def canon(value: Any) -> str:
    """The comparator. ``sort_keys`` because field order is not the contract;
    ``default=repr`` so an unserializable value is a visible difference rather
    than an exception."""
    return json.dumps(value, sort_keys=True, default=repr)


def _strip_row_ids(value: Any) -> Any:
    """The CSV carries ``__row_id``; the Arrow file never projects one. Not a
    format difference — the two writers inject it at different moments."""
    if isinstance(value, list):
        return [
            {k: v for k, v in r.items() if k != ROW_ID} if isinstance(r, dict) else r
            for r in value
        ]
    return value


def both(rows: list, *, strict: bool = True, drop_types: bool = False) -> tuple[Any, Any]:
    """One value, both artifacts, both readings.

    Written by the SAME call — ``jsonl_to_csv_and_nested_arrow`` is one scan
    feeding two writers, and the crate asserts its CSV bytes are byte-identical
    to the CSV-only path. So any difference below is a difference in what the
    FORMATS can express, never in what was measured.
    """
    d = tempfile.mkdtemp(prefix="arrow-parity-")
    inp = os.path.join(d, "in.jsonl")
    csv_path = os.path.join(d, "out.csv")
    arrow_path = os.path.join(d, "out.arrow")
    with open(inp, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str))
            fh.write("\n")

    _cols, types, _n, item_kind = jcs.jsonl_to_csv_and_nested_arrow(
        inp, csv_path, arrow_path,
    )
    if drop_types:
        types = None  # a ref written before column_types existed
    with open(csv_path, "rb") as fh:
        csv_bytes = fh.read()
    with open(arrow_path, "rb") as fh:
        arrow_bytes = fh.read()

    csv_value = _strip_row_ids(
        CsvConverter.csv_to_dict(csv_bytes, types, value_kind=ARRAY, item_kind=item_kind)
    )
    arrow_value = _strip_row_ids(
        CsvConverter.rows_to_value(
            jcs.arrow_bytes_to_rows(arrow_bytes, strict), ARRAY, item_kind,
        )
    )
    return csv_value, arrow_value


# ---------------------------------------------------------------------------
# The corpus, by bucket
# ---------------------------------------------------------------------------

AGREE: list[tuple[str, list]] = [
    ("objects_flat", [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]),
    # SA-1984, the half a nullable Struct simply absorbs
    ("null_beside_object", [{"x": None}, {"x": {"a": 1}}]),
    # SA-1984, the hard half — the writer picks one type at schema time
    ("scalar_beside_object", [{"x": 5}, {"x": {"a": 1}}]),
    # the payload that becomes 3 CSV files under RECURSIVE_ARTIFACT_SPLIT
    ("nested_orders_items", [{"orders": [{"id": 1, "items": [{"sku": "A"}]}]}]),
    ("empty_vs_null_list", [{"a": [1]}, {"a": []}, {"a": None}]),
    ("leading_zero_string", [{"zip": "03230"}]),
    ("numeric_lookalikes", [{"a": "007", "b": "1e5", "c": "+1", "d": "00", "e": "3.10"}]),
    # `Float64` would erase the int/float literal distinction `_cast_value` keeps
    ("mixed_int_float", [{"n": 1}, {"n": 1.5}]),
    ("big_int_2p53", [{"id": 9007199254740993}]),
    ("big_int_1e25", [{"id": 10000000000000000000000000}]),
    ("mixed_leaf_token", [{"n": 5}, {"n": "N/A"}]),
    ("bools", [{"ok": True}, {"ok": False}]),
    ("value_mode_numbers", [1, 2, 3]),
    ("value_mode_strings_null", ["a", None, "b"]),
    ("value_mode_empty_string", ["a", "", "b"]),
    ("value_mode_lists", [[1, 2], [3, 4]]),
    # a null LINE is a value, not an empty record — both writers agree, and the
    # nested writer had to be fixed to make that true
    ("sparse_objects_with_null", [{"id": 1}, None, {"id": 3}]),
    ("unicode_delims", [{"t": 'a,b "q" ç'}]),
]

ARROW_BETTER: list[tuple[str, list, Any]] = [
    # CSV has one cell per column, so a list beside a scalar is stored as JSON
    # TEXT and read back as the string. Arrow keeps both shapes.
    ("scalar_beside_array", [{"x": 5}, {"x": [1, 2]}], [{"x": 5}, {"x": "[1,2]"}]),
]

BOTH_WIDEN: list[tuple[str, list, Any]] = [
    # A rectangular format gives every row every column, so a key absent on one
    # row comes back present and null. Arrow's Struct does the same: the
    # validity bit is per VALUE, not per "was this key written".
    (
        "objects_sparse",
        [{"a": 1}, {"b": 2}],
        [{"a": 1, "b": None}, {"a": None, "b": 2}],
    ),
]

ARROW_BETTER_NOT_EXACT: list[tuple[str, list, Any, Any]] = [
    # SA-1988. CSV writes the same empty cell for "absent" and for "", so the
    # genuine empty string dies. Arrow keeps it — and still adds the phantom
    # key on row 0, which is the residual above.
    (
        "empty_string_vs_missing",
        [{"a": 1}, {"a": 1, "late": ""}],
        [{"a": 1, "late": None}, {"a": 1, "late": None}],
        [{"a": 1, "late": None}, {"a": 1, "late": ""}],
    ),
    # A nested branch absent on one row. CSV rebuilds the whole branch to hold a
    # phantom leaf (`d: {x: None}`); Arrow says the branch itself was absent
    # (`d: None`), which is nearer the truth without being it.
    (
        "nested_branch_absent",
        [{"m": {"w": "W1", "d": {"x": 1}}}, {"m": {"w": "W2"}}],
        [{"m": {"w": "W1", "d": {"x": 1}}}, {"m": {"w": "W2", "d": {"x": None}}}],
        [{"m": {"w": "W1", "d": {"x": 1}}}, {"m": {"w": "W2", "d": None}}],
    ),
]


# ---------------------------------------------------------------------------
# The contracts
# ---------------------------------------------------------------------------


def test_the_two_writers_agree_on_the_reserved_names():
    from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
    from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
    from app.layer1_domain.value_objects.value_kind import VALUE_COLUMN

    assert jcs.VALUE_COLUMN == VALUE_COLUMN
    assert jcs.ROW_ID_COLUMN == ROW_ID_COLUMN
    # A nested file_ref is a POINTER; the tree sketch refuses to recurse into
    # one, and it can only refuse if it spells the marker the same way.
    assert jcs.FILE_REF_MARKER == FILE_REF_MARKER


@pytest.mark.parametrize(("name", "rows"), AGREE, ids=[n for n, _ in AGREE])
def test_agree(name, rows):
    csv_value, arrow_value = both(rows)
    assert canon(arrow_value) == canon(rows), f"{name}: arrow lost the original"
    assert canon(csv_value) == canon(rows), f"{name}: csv lost the original"


@pytest.mark.parametrize(
    ("name", "rows", "csv_pinned"), ARROW_BETTER, ids=[n for n, _, _ in ARROW_BETTER],
)
def test_arrow_better(name, rows, csv_pinned):
    csv_value, arrow_value = both(rows)
    assert canon(arrow_value) == canon(rows), f"{name}: arrow is supposed to be exact"
    assert canon(csv_value) == canon(csv_pinned), f"{name}: the CSV loss moved"


@pytest.mark.parametrize(
    ("name", "rows", "pinned"), BOTH_WIDEN, ids=[n for n, _, _ in BOTH_WIDEN],
)
def test_both_widen(name, rows, pinned):
    csv_value, arrow_value = both(rows)
    assert canon(csv_value) == canon(pinned), f"{name}: csv widening moved"
    assert canon(arrow_value) == canon(pinned), f"{name}: arrow widening moved"


@pytest.mark.parametrize(
    ("name", "rows", "csv_pinned", "arrow_pinned"),
    ARROW_BETTER_NOT_EXACT,
    ids=[n for n, _, _, _ in ARROW_BETTER_NOT_EXACT],
)
def test_arrow_better_but_not_exact(name, rows, csv_pinned, arrow_pinned):
    csv_value, arrow_value = both(rows)
    assert canon(csv_value) == canon(csv_pinned), f"{name}: csv reading moved"
    assert canon(arrow_value) == canon(arrow_pinned), f"{name}: arrow reading moved"
    # The bucket's own claim: Arrow is different from CSV, and neither is exact.
    assert canon(arrow_value) != canon(csv_value)
    assert canon(arrow_value) != canon(rows)


def test_the_type_travels_in_the_file_not_in_a_map_that_can_go_missing():
    """A ref written before ``column_types`` existed. The CSV reader falls back
    to ``_try_parse_value``, which runs ``int("03230")`` and destroys the id.
    Arrow cannot have this failure mode: the type is IN the file."""
    csv_value, arrow_value = both([{"zip": "03230"}], drop_types=True)
    assert canon(csv_value) == canon([{"zip": 3230}])
    assert canon(arrow_value) == canon([{"zip": "03230"}])


def test_strict_parity_only_moves_a_heterogeneous_scalar_column():
    """The one place the two readings legitimately differ on a homogeneous type.

    Everything else was fixed in the WRITER instead — a column holding both
    integer and fractional literals keeps the literals rather than becoming
    ``Float64``, so there is nothing left for the reader to patch up.
    """
    rows = [{"n": 5}, {"n": "N/A"}]
    _csv, strict = both(rows, strict=True)
    _csv2, loose = both(rows, strict=False)
    assert canon(strict) == canon([{"n": 5}, {"n": "N/A"}])
    assert canon(loose) == canon([{"n": "5"}, {"n": "N/A"}])

    # and it moves NOTHING on a homogeneous corpus
    for name, homogeneous in AGREE:
        if name == "mixed_leaf_token":
            continue
        a = both(homogeneous, strict=True)[1]
        b = both(homogeneous, strict=False)[1]
        assert canon(a) == canon(b), f"{name}: strict parity should be a no-op here"


def test_the_comparator_is_not_equality():
    """Guard on the guard. Every assertion above would pass under ``==`` for the
    exact type divergences this file exists to catch."""
    assert 1 == 1.0 and True == 1  # noqa: E712 - that is the point
    assert canon(1) != canon(1.0)
    assert canon(True) != canon(1)
