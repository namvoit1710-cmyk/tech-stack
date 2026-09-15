"""SA-1905 Tier 4 — differential fuzz over nested JSON.

``test_arrow_nested_parity`` documents the shapes we thought of. This looks for
the ones we did not. Both are needed and neither substitutes for the other: a
corpus is a list of remembered bugs, and a fuzzer is bad at explaining what it
found but very good at finding it.

# The property

**Arrow never loses a value.** For every path present in the original, the value
read back through the sidecar is equal — same value AND same type, compared as a
canonical JSON dump because ``1 == 1.0`` and ``True == 1`` in Python.

Extra keys are tolerated **only when their value is null**. That is the one
residual this design does not fix and does not claim to: Arrow's ``Struct``
carries a validity bit per VALUE, not a "was this key written on this row" bit,
so a record missing a key its siblings have comes back with the key present and
null. Pinned in ``test_arrow_nested_parity.ARROW_BETTER_NOT_EXACT``; anything
else is a real failure.

Deliberately NOT asserted: ``arrow == csv``. The two formats can express
different things — that is the entire reason this work exists — so a fuzz
demanding they agree would be a fuzz demanding Arrow reproduce SA-1984 and
SA-1988.

# The leaf alphabet

Seeded, not random, with every value that has already cost something:
``""`` (SA-1988), ``"0123"`` (``_try_parse_value``), ``2**53 + 1`` (the f64
collapse), ``10**25`` (beyond i64), ``[]`` and ``{}`` (empty vs null), and the
strings ``"true"`` / ``"null"`` / ``"1e5"`` that look like other types.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from tests.unit._native_streamer import load as _load_native  # noqa: E402

jcs = _load_native()

from app.layer1_domain.value_objects.value_kind import ARRAY  # noqa: E402
from app.layer2_application.data.csv_converter import CsvConverter  # noqa: E402

ROW_ID = jcs.ROW_ID_COLUMN


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

# Reserved names are excluded on purpose. A DATA key literally called
# ``__row_id`` collides with the CSV's synthetic column, which is a pre-existing
# hazard of that format and has nothing to say about this encoding — letting the
# fuzzer find it would just be noise pointed at the wrong module.
KEYS = st.sampled_from(["a", "b", "c", "id", "name", "meta", "items", "x.y"])

LEAVES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(2**62), max_value=2**62),
    # every integer that has already cost something
    st.sampled_from([0, -1, 2**53, 2**53 + 1, -(2**53) - 1, 10**25, -(10**25)]),
    st.floats(allow_nan=False, allow_infinity=False),
    # …and every string that looks like some other type
    st.sampled_from(
        ["", " ", "0123", "007", "1e5", "N/A", "true", "false", "null",
         "a,b", 'q"q', "ç", "[]", "{}", "3.10", "+1"]
    ),
    st.text(max_size=6),
)


def json_value(depth: int) -> st.SearchStrategy[Any]:
    if depth <= 0:
        return LEAVES
    inner = json_value(depth - 1)
    return st.one_of(
        LEAVES,
        st.lists(inner, max_size=3),
        st.dictionaries(KEYS, inner, max_size=3),
    )


# A node output is a LIST of values — that is what `store_data` is handed.
ROWS = st.lists(json_value(3), min_size=1, max_size=5)


# ---------------------------------------------------------------------------
# The property
# ---------------------------------------------------------------------------


def canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, default=repr)


def structural_faults(got: Any, want: Any, path: str = "$") -> str:
    """``""`` when *got* has the same SHAPE as *want* and lost nothing to null.

    Two universal invariants, and deliberately not a third:

    * **Nothing non-null becomes null.** That is the worst failure mode a typed
      encoder has — the first version of this writer put ``parse::<i64>().ok()``
      on a column whose declaration was only the first row's leaf type, and
      ``"N/A"`` became a silent ``None``. A value that changes TYPE is visible;
      a value that becomes null is gone.
    * **Structure is preserved.** Lists keep their length, objects keep their
      keys. An extra key is tolerated only when it is null — the ``Struct``
      residual, pinned in ``test_arrow_nested_parity``.

    NOT asserted: that the value is unchanged. A path holding more than one
    scalar type has no single honest type, so the writer stores the token and
    the reader casts it by the column's declared kind — ``[false, 0]`` reads
    back ``[False, "0"]`` — and **the CSV reader does exactly the same thing**.
    Demanding equality here would demand Arrow guess better than the format it
    is mirroring. Exactness is asserted where it IS owed, on type-homogeneous
    data, below.

    Returns a reason rather than a bool: a fuzzer that only says "False" costs
    more to debug than it saves.
    """
    if want is not None and got is None:
        return f"{path}: {want!r} became null"
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return f"{path}: expected an object, got {type(got).__name__}"
        for k, v in want.items():
            if k not in got:
                return f"{path}.{k}: key lost"
            why = structural_faults(got[k], v, f"{path}.{k}")
            if why:
                return why
        for k in got.keys() - want.keys():
            if got[k] is not None:
                return f"{path}.{k}: invented a non-null value {got[k]!r}"
        return ""
    if isinstance(want, list):
        if not isinstance(got, list):
            return f"{path}: expected a list, got {type(got).__name__}"
        if len(got) != len(want):
            return f"{path}: length {len(got)} != {len(want)}"
        for i, (g, w) in enumerate(zip(got, want)):
            why = structural_faults(g, w, f"{path}[{i}]")
            if why:
                return why
        return ""
    return ""


def _read_back_arrow(rows: list) -> Any:
    d = tempfile.mkdtemp(prefix="arrow-fuzz-")
    try:
        inp = os.path.join(d, "in.jsonl")
        csv_path = os.path.join(d, "out.csv")
        arrow_path = os.path.join(d, "out.arrow")
        with open(inp, "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, default=str))
                fh.write("\n")
        _cols, _types, _n, item_kind = jcs.jsonl_to_csv_and_nested_arrow(
            inp, csv_path, arrow_path,
        )
        with open(arrow_path, "rb") as fh:
            blob = fh.read()
        value = CsvConverter.rows_to_value(
            jcs.arrow_bytes_to_rows(blob, True), ARRAY, item_kind,
        )
        if isinstance(value, list):
            value = [
                {k: v for k, v in r.items() if k != ROW_ID} if isinstance(r, dict) else r
                for r in value
            ]
        return value
    finally:
        import shutil

        shutil.rmtree(d, ignore_errors=True)


@settings(
    max_examples=300,
    deadline=None,  # each example writes two files and shells into Rust
    suppress_health_check=[HealthCheck.too_slow],
)
@given(rows=ROWS)
def test_nothing_becomes_null_and_no_structure_is_lost(rows):
    why = structural_faults(_read_back_arrow(rows), rows)
    assert not why, f"{why}\n  input: {canon(rows)}"


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(rows=ROWS)
def test_the_row_count_survives(rows):
    """The declaration is load-bearing: ``_items_total`` sizes a lazy LOOP from
    ``row_count``, so a file that holds fewer rows than the ref claims runs a
    phantom iteration on a null item."""
    assert len(_read_back_arrow(rows)) == len(rows)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(rows=st.lists(json_value(2), min_size=1, max_size=4))
def test_reading_twice_gives_the_same_value(rows):
    """Determinism. A schema that depended on iteration order — a `HashMap`
    where an `IndexMap` was meant — would show up here and nowhere else."""
    assert canon(_read_back_arrow(rows)) == canon(_read_back_arrow(rows))


def _type_homogeneous(rows: list[dict], keys: set) -> bool:
    """Every key holds one JSON type across every row (nulls abstain).

    ``type()`` and not ``isinstance``, deliberately: ``bool`` is a subclass of
    ``int`` in Python, and ``False`` beside ``0`` is exactly the heterogeneity
    this predicate has to catch.
    """
    for k in keys:
        kinds = {type(r[k]).__name__ for r in rows if r[k] is not None}
        if len(kinds) > 1:
            return False
    return True


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(rows=st.lists(st.dictionaries(KEYS, LEAVES, min_size=1, max_size=4), min_size=1, max_size=5))
def test_flat_homogeneous_records_round_trip_exactly(rows):
    """The strongest form of the property, on the shape most real node output
    has: same keys everywhere, one type per key.

    Both escape hatches are closed here — no sparsity, so the ``Struct``
    residual cannot fire; and no scalar heterogeneity, so no column falls back
    to ``Token``. Anything short of an exact round trip is a defect.

    The first version of this test asserted the same thing after checking only
    the key sets, and the fuzzer immediately produced ``[{"b": false}, {"b": 0}]``
    — a legitimately lossy ``Token`` column that CSV reads the same way. The
    premise was wrong, not the code.
    """
    keys = set(rows[0])
    hypothesis.assume(all(set(r) == keys for r in rows))
    hypothesis.assume(_type_homogeneous(rows, keys))
    assert canon(_read_back_arrow(rows)) == canon(rows)
