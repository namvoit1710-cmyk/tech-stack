"""SA-1905 F3 — golden-corpus parity: the Rust producer vs ``CsvConverter``.

``libs/json-csv-streamer/src/lib.rs`` and ``CsvConverter`` / ``value_kind`` are
TWO WRITERS OF ONE FORMAT. The crate says so itself, above its own constant::

    MUST match ``value_kind.VALUE_COLUMN`` on the control plane: this crate and
    the Python ``CsvConverter`` are two writers of one format, and a corpus that
    only ran against one of them would not notice them drifting apart.

Until this file existed, that agreement was held by that comment and by nothing
else. Three rules are hand-mirrored across the language boundary — the reserved
column names, ``item_kind``, and ``value_cells_are_json`` — and drift in any of
them is a silent data-corruption bug, not an exception: a corpus of files whose
declared shape no longer matches their own bytes. Nobody finds that until a
reader hands back ``[{"__value": {...}}, ...]`` and ``$loop.item.id`` resolves to
nothing.

Every case runs through BOTH writers and is compared on the four things a reader
actually depends on: the declared ``item_kind``, the declared column types, the
data columns, and what ``csv_to_dict`` gives back.

**Comparison is at round-trip level, not byte level, deliberately.** Python's
``csv`` module terminates rows with ``\\r\\n`` and the Rust ``csv`` crate with
``\\n``, so the two files differ in bytes while meaning exactly the same thing.
Byte identity was never the contract — ``lib.rs`` states that in its module
docstring — and asserting it would fail for a reason nobody cares about while
still missing the reasons everybody cares about.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import tempfile

import pytest

from tests.unit._native_streamer import load as _load_native

jcs = _load_native()

from app.layer1_domain.value_objects.csv_document import ROW_ID_COLUMN
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    VALUE_COLUMN,
    item_kind_of_inline,
)
from app.layer2_application.data.csv_converter import CsvConverter

# ---------------------------------------------------------------------------
# The corpus. Each case is a node output as a worker would return it.
#
# An EMPTY list is deliberately absent: both `should_stream` gates require a
# non-empty list, so neither writer is ever handed one in production, and the
# two disagree on it (Python returns b"", Rust writes a header-only file).
# Pinning a disagreement that cannot occur would be pinning noise.
# ---------------------------------------------------------------------------
CORPUS: list[tuple[str, list]] = [
    # --- object mode: the rows ARE the columns ---
    ("objects_flat", [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]),
    ("objects_sparse", [{"a": 1}, {"b": 2}]),
    ("objects_nested", [{"m": {"w": "W1", "d": {"x": 1}}}, {"m": {"w": "W2"}}]),
    ("objects_with_arrays", [{"tags": ["a", "b"]}, {"tags": []}]),
    ("objects_with_nulls", [{"a": None, "b": 1}, {"a": "s", "b": None}]),
    ("objects_all_empty", [{}, {}]),
    ("objects_bools", [{"ok": True}, {"ok": False}]),
    ("objects_big_integer", [{"id": 10**25}]),
    ("objects_unicode_and_delimiters", [{"t": 'a,b "q" ç\tz'}]),
    # The trap `uses_value_column` exists to defuse: a legitimate list of
    # one-key dicts whose key happens to BE the reserved column. Object mode.
    ("objects_named_like_the_reserved_column", [{"__value": 1}, {"__value": 2}]),
    # --- value mode: anything a CSV row cannot be ---
    ("scalars_numbers", [1, 2, 3]),
    ("scalars_strings", ["a", "b"]),
    ("scalars_booleans", [True, False]),
    ("scalars_all_null", [None, None]),
    # string + null: `""` and null would collide, so cells must be JSON
    ("scalars_strings_with_null", ["a", None, "b"]),
    ("scalars_numbers_with_null", [1, None, 3]),
    ("mixed_scalars", [1, "a", True]),
    ("list_of_lists", [[1, 2], [3, 4]]),
    # THE SA-1977 case: a sparse array of objects. Must NOT declare
    # `array<object>` — those rows went to `__value`.
    ("sparse_objects_with_null", [{"id": 1}, None, {"id": 3}]),
    ("objects_mixed_with_scalar", [{"a": 1}, "loose"]),
]

IDS = [name for name, _ in CORPUS]
ROWS = [rows for _, rows in CORPUS]

# ---------------------------------------------------------------------------
# Cases that do NOT round-trip to the value the worker returned — and why that
# is the format, not a defect in either writer.
#
# A CSV is rectangular: the header is the union of every row's keys, so a row
# that lacked a key still gets that column, and an absent cell is byte-identical
# to an explicit null. `_unflatten` therefore rebuilds it as `None` — and for a
# dotted column, rebuilds the whole intermediate branch to hold it.
#
# This is NOT drift: `test_the_files_read_back_identically` covers these same two
# cases and passes, i.e. `dict_to_csv` widens them exactly the same way. It is a
# property of choosing CSV (steward-editable, OData-pageable) as the storage
# format, and it predates this crate.
#
# Pinned rather than asserted-away: `{"m": {"w": "W2"}}` coming back with a
# phantom `m.d.x = None` is the surprising one — a whole nested branch appears
# that the worker never sent — and a LOOP body reading `$loop.item.m.d.x` gets
# `None` instead of a missing key. If that behaviour ever changes, on either
# side, this test is where it shows up.
# ---------------------------------------------------------------------------
WIDENED: dict[str, list] = {
    "objects_sparse": [{"a": 1, "b": None}, {"a": None, "b": 2}],
    "objects_nested": [
        {"m": {"w": "W1", "d": {"x": 1}}},
        {"m": {"w": "W2", "d": {"x": None}}},
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rust(rows: list) -> tuple[list[str], dict[str, str], int, str, bytes]:
    """Run the corpus case through the native producer."""
    d = tempfile.mkdtemp(prefix="jcs-parity-")
    try:
        inp, outp = os.path.join(d, "in.jsonl"), os.path.join(d, "out.csv")
        with open(inp, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str))
                fh.write("\n")
        columns, types, n, item_kind = jcs.jsonl_to_csv(inp, outp)
        with open(outp, "rb") as fh:
            return columns, types, n, item_kind, fh.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _value_mode(rows: list) -> bool:
    """The storage decision, mirroring ``DataStoreService._is_value_root``:
    a list with ANY non-dict element goes to the reserved column."""
    return not all(isinstance(r, dict) for r in rows)


def _python(rows: list) -> tuple[list[str], dict[str, str], int, str, bytes]:
    """Run the same case through the control plane's writer."""
    if _value_mode(rows):
        blob, _name, n = CsvConverter.values_to_csv(rows)
        types = CsvConverter.infer_value_column_types(rows)
        columns = [VALUE_COLUMN]
    else:
        blob, _name, n = CsvConverter.dict_to_csv(rows)
        types = CsvConverter.infer_column_types(rows)
        columns = _data_columns(blob)
    return columns, types, n, item_kind_of_inline(rows), blob


def _data_columns(blob: bytes) -> list[str]:
    """Header minus the synthetic row id — the two writers inject it at
    different moments (Rust in PASS 2, Python in ``inject_row_ids``), which is
    not a format difference."""
    if not blob:
        return []
    header = next(csv.reader(io.StringIO(blob.decode("utf-8"))), [])
    return [c for c in header if c != ROW_ID_COLUMN]


def _read_back(blob: bytes, types: dict[str, str], item_kind: str) -> list:
    """What a reader gets. A JSONL file IS a list of its lines, so the root kind
    is never in question — it is always ``array`` (``lib.rs`` says the same)."""
    out = CsvConverter.csv_to_dict(blob, types, value_kind=ARRAY, item_kind=item_kind)
    return [
        {k: v for k, v in row.items() if k != ROW_ID_COLUMN} if isinstance(row, dict) else row
        for row in out
    ]


# ---------------------------------------------------------------------------
# The constants: one renamed column on either side is a corpus of unreadable
# files. These are the cheapest assertions in the repo and they were missing.
# ---------------------------------------------------------------------------


def test_the_two_writers_agree_on_the_reserved_column_names():
    assert jcs.VALUE_COLUMN == VALUE_COLUMN
    assert jcs.ROW_ID_COLUMN == ROW_ID_COLUMN


def test_the_two_writers_share_one_kind_vocabulary():
    from app.layer1_domain.value_objects import value_kind as vk

    assert set(jcs.ITEM_KINDS) == set(vk.KINDS) | {vk.UNKNOWN}


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rows", ROWS, ids=IDS)
def test_item_kind_agrees(rows):
    """`item_kind` IS the storage decision, so a disagreement here is a reader
    that unwraps what the writer did not wrap (or the reverse)."""
    assert _rust(rows)[3] == _python(rows)[3]


@pytest.mark.parametrize("rows", ROWS, ids=IDS)
def test_row_count_agrees(rows):
    assert _rust(rows)[2] == _python(rows)[2] == len(rows)


@pytest.mark.parametrize("rows", ROWS, ids=IDS)
def test_data_columns_agree(rows):
    r_cols, _, _, _, r_blob = _rust(rows)
    p_cols, _, _, _, _ = _python(rows)
    assert r_cols == p_cols
    # and the crate's returned column list must match the file it just wrote
    assert r_cols == _data_columns(r_blob)


@pytest.mark.parametrize("rows", ROWS, ids=IDS)
def test_declared_column_types_agree(rows):
    """A column's declared type is what `_cast_value` trusts on the way back. If
    the two writers declare differently, the same bytes decode to different
    Python objects depending on who wrote them."""
    assert _rust(rows)[1] == _python(rows)[1]


@pytest.mark.parametrize("rows", ROWS, ids=IDS)
def test_the_files_read_back_identically(rows):
    """The assertion that subsumes the rest: whatever the bytes look like, a
    reader must not be able to tell which writer produced them."""
    r_cols, r_types, _, r_item, r_blob = _rust(rows)
    p_cols, p_types, _, p_item, p_blob = _python(rows)
    assert _read_back(r_blob, r_types, r_item) == _read_back(p_blob, p_types, p_item)


@pytest.mark.parametrize(("name", "rows"), CORPUS, ids=IDS)
def test_the_native_file_round_trips_to_the_original_value(name, rows):
    """The end-to-end contract: what a worker returned is what a reader gets.

    ``WIDENED`` names the cases where CSV's rectangularity makes that literally
    impossible; those assert the widened value instead, so the behaviour is
    pinned rather than excused.
    """
    _cols, types, _n, item_kind, blob = _rust(rows)
    assert _read_back(blob, types, item_kind) == WIDENED.get(name, rows)


def test_the_widening_cases_are_a_format_property_not_a_writer_difference():
    """Guard on the guard: if `dict_to_csv` ever stopped widening, `WIDENED`
    would be documenting a Rust-only behaviour — which is exactly the drift this
    file exists to catch. Assert both writers still widen identically."""
    for name, rows in CORPUS:
        if name not in WIDENED:
            continue
        _c, p_types, _n, p_item, p_blob = _python(rows)
        assert _read_back(p_blob, p_types, p_item) == WIDENED[name], name
