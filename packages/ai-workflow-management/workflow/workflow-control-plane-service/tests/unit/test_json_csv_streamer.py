"""SA-1905 A1/B1 — streaming JSON(L) -> CSV (Rust/PyO3), round-trip compatible
with CsvConverter.

The Rust producer must write CSV that the control plane reads back byte-for-byte
correctly via ``CsvConverter.csv_to_dict`` (the resolver/materialize contract).
These tests assert the round-trip, sparse-column union (PASS 1 sketch), and the
synthetic ``__row_id`` that makes the output upload-ready.

Skipped when the native ``json_csv_streamer`` wheel isn't installed
(``maturin build`` in ``libs/json-csv-streamer``).
"""

from __future__ import annotations

import json

import pytest

from tests.unit._native_streamer import load as _load_native

# SA-1905 F1 — was `pytest.importorskip("json_csv_streamer")`. That skipped
# silently in CI, which never built the wheel, so these 13 tests read as passes
# while covering nothing. `load()` still skips on a laptop without the wheel and
# FAILS when CI says it built one.
jcs = _load_native()

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


def _strip_ids(rows):
    return DataStoreService._strip_synthetic_columns(rows)


def test_roundtrip_scalars_nested_and_arrays(tmp_path):
    rows = [
        {"sku": "A", "qty": 3, "price": 9.5, "meta": {"warehouse": "W1"}, "tags": ["x", "y"], "ok": True},
        {"sku": "B", "qty": 10, "price": 0.0, "meta": {"warehouse": "W2"}, "tags": [], "ok": False},
    ]
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, rows)

    columns, types, n, item_kind = jcs.jsonl_to_csv(str(inp), str(out))
    assert n == 2
    assert item_kind == "object"  # SA-1977 — the element kind, declared not guessed
    assert "__row_id" not in columns  # synthetic, written by the CSV writer, not a data column
    assert types["qty"] == "number" and types["ok"] == "boolean" and types["tags"] == "json"
    assert types["meta.warehouse"] == "string"

    restored = _strip_ids(CsvConverter.csv_to_dict(
        out.read_bytes(), types, value_kind="array", item_kind="object",
    ))
    assert restored == rows, "Rust CSV must round-trip through CsvConverter losslessly"


def test_row_id_is_written_and_sequential(tmp_path):
    rows = [{"a": 1}, {"a": 2}, {"a": 3}]
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, rows)
    jcs.jsonl_to_csv(str(inp), str(out))

    header = out.read_bytes().splitlines()[0].decode()
    assert header.split(",")[0] == "__row_id", "row_id must be the first column (upload-ready)"
    raw = CsvConverter.csv_to_dict(out.read_bytes(), {"a": "number"})
    assert [r["__row_id"] for r in raw] == [0, 1, 2]


def test_sparse_column_union_pass1(tmp_path):
    # A field first seen at the LAST row must still make the column union
    # (streaming presence-count sketch, not just row-0's keys).
    rows = [{"a": 1}, {"a": 2}, {"a": 3, "late": "hi"}]
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, rows)

    columns, types, n, _item = jcs.schema_sketch(str(inp))
    assert "late" in columns and types["late"] == "string"
    assert n == 3

    jcs.write_csv(str(inp), str(out), columns)
    restored = _strip_ids(CsvConverter.csv_to_dict(
        out.read_bytes(), types, value_kind="array", item_kind="object",
    ))
    assert restored[0].get("late") is None  # absent -> empty cell -> None
    assert restored[2]["late"] == "hi"


def test_single_object_line(tmp_path):
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, [{"status": 200, "body": "ok"}])
    columns, types, n, item_kind = jcs.jsonl_to_csv(str(inp), str(out))
    assert n == 1
    assert item_kind == "object"
    # SA-1977 — a JSONL file IS a list, so ONE line is a ONE-ELEMENT LIST. Read
    # with the declared kind it stays one; the old row-count guess turned it into
    # a bare dict, which is the silent-data-loss half of this Story.
    restored = _strip_ids(CsvConverter.csv_to_dict(
        out.read_bytes(), types, value_kind="array", item_kind="object",
    ))
    assert restored == [{"status": 200, "body": "ok"}]


def test_empty_and_blank_lines_skipped(tmp_path):
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    inp.write_text('{"a": 1}\n\n{"a": 2}\n', encoding="utf-8")
    columns, types, n, _item = jcs.jsonl_to_csv(str(inp), str(out))
    assert n == 2


# ---------------------------------------------------------------------------
# SA-1977 A4 — the streamer must speak the reserved ``__value`` column too, or
# the two producers store the same list two different ways.
# ---------------------------------------------------------------------------


def test_scalar_list_uses_the_reserved_value_column(tmp_path):
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, [1, 2, 3])
    columns, types, n, item_kind = jcs.jsonl_to_csv(str(inp), str(out))
    assert columns == ["__value"] and types == {"__value": "number"}
    assert n == 3 and item_kind == "number"
    restored = _strip_ids(CsvConverter.csv_to_dict(
        out.read_bytes(), types, value_kind="array", item_kind=item_kind,
    ))
    assert restored == [1, 2, 3], "scalars, not wrapper dicts"


def test_mixed_kind_list_json_encodes_every_cell(tmp_path):
    # A column whose type depends on which element came FIRST is the bug this
    # Story removes; a mixed list declares one type (json) and encodes each cell.
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, [1, "a", True, None])
    columns, types, n, item_kind = jcs.jsonl_to_csv(str(inp), str(out))
    assert columns == ["__value"] and types == {"__value": "json"}
    assert item_kind == "unknown"
    restored = _strip_ids(CsvConverter.csv_to_dict(
        out.read_bytes(), types, value_kind="array", item_kind=item_kind,
    ))
    assert restored == [1, "a", True, None]


def test_any_non_object_line_puts_the_whole_file_in_value_mode(tmp_path):
    # One scalar among objects must NOT emit both object columns and __value —
    # that is precisely the leaf/branch collision (SA-1984) in another guise.
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    _write_jsonl(inp, [{"a": 1}, 7])
    columns, types, _n, item_kind = jcs.jsonl_to_csv(str(inp), str(out))
    assert columns == ["__value"] and item_kind == "unknown"
    restored = _strip_ids(CsvConverter.csv_to_dict(
        out.read_bytes(), types, value_kind="array", item_kind=item_kind,
    ))
    assert restored == [{"a": 1}, 7]


def test_invalid_json_line_raises(tmp_path):
    inp, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
    inp.write_text('{"a": 1}\n{not valid}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        jcs.jsonl_to_csv(str(inp), str(out))


class TestTheGilIsReleasedForTheWholeConversion:
    """REGRESSION. No exported function used to call ``py.allow_threads``, so a
    conversion froze the whole interpreter for its entire duration.

    Measured on a 561 MB payload before the fix: **39.7 s** with nothing else in
    the process running — a 100 ms ticker fired twice instead of ~396 times.
    Frozen with it: the worker heartbeat (``HEARTBEAT_INTERVAL_SECONDS`` is 30,
    so at least one beat is missed) and lease renewal, which is an asyncio task
    and therefore cannot fire whatever ``lease_ttl_seconds`` the executor set. A
    lapsed lease is not a slow task; it is a task the executor hands to another
    worker while this one is still running it.

    These run on a small input, so they assert the PROPERTY (other threads make
    progress) rather than a duration — a timing threshold would be flaky on a
    loaded CI box, and the property is what actually broke.
    """

    _TICK = 0.005

    @classmethod
    def _assert_other_threads_ran(cls, label, fn):
        """Run ``fn`` with a 5 ms ticker thread and assert the ticker kept ticking.

        ``ticks > 0`` is NOT sufficient and was the first version of this test: a
        thread starved by a held GIL still records ONE gap the moment it finally
        reacquires, so that assertion passed against the un-fixed wheel and
        proved nothing. The two signals that actually separate the states:

        * a ratio of observed to expected ticks — a held GIL yields ~1, a
          released one yields most of them;
        * the longest single gap — a held GIL makes it the WHOLE conversion,
          a released one keeps it near the tick period.

        Both are asserted loosely enough to survive a loaded CI box (a quarter of
        the expected ticks; half the conversion) while still being orders of
        magnitude away from the un-fixed behaviour.
        """
        import threading
        import time

        gaps, stop = [], threading.Event()

        def ticker():
            last = time.monotonic()
            while not stop.is_set():
                time.sleep(cls._TICK)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        t = threading.Thread(target=ticker, daemon=True)
        t.start()
        time.sleep(0.05)
        start = len(gaps)
        t0 = time.monotonic()
        fn()
        elapsed = time.monotonic() - t0
        stop.set()
        t.join(timeout=2)

        during = gaps[start:] or [0.0]
        ticks, worst, expected = len(during), max(during), elapsed / cls._TICK
        detail = (
            f"{label}: conversion took {elapsed:.2f}s; the ticker should have fired "
            f"~{expected:.0f} times and fired {ticks}; longest single stall "
            f"{worst:.2f}s. The GIL was held for the conversion — every "
            f"#[pyfunction] in libs/json-csv-streamer must wrap its work in "
            f"py.allow_threads."
        )
        assert ticks >= expected * 0.25, detail
        assert worst < elapsed * 0.5, detail

    def _big_input(self, tmp_path):
        # Sized so the conversion lasts ~0.5-1 s: long enough that a held GIL
        # starves the 5 ms ticker by two orders of magnitude, short enough to
        # keep the suite fast.
        src = tmp_path / "big.jsonl"
        _write_jsonl(src, [
            {"id": i, "sku": f"SKU-{i:06d}", "meta": {"lane": i % 7}, "tags": ["a", "b"]}
            for i in range(150_000)
        ])
        return src

    def test_jsonl_to_csv_lets_other_threads_run(self, tmp_path):
        """The call the worker actually makes — both passes under one release."""
        src, out = self._big_input(tmp_path), tmp_path / "out.csv"

        self._assert_other_threads_ran(
            "jsonl_to_csv", lambda: jcs.jsonl_to_csv(str(src), str(out))
        )

    def test_schema_sketch_lets_other_threads_run(self, tmp_path):
        src = self._big_input(tmp_path)

        self._assert_other_threads_ran(
            "schema_sketch", lambda: jcs.schema_sketch(str(src))
        )

    def test_write_csv_lets_other_threads_run(self, tmp_path):
        src, out = self._big_input(tmp_path), tmp_path / "out.csv"
        columns, _types, _rows, _kind = jcs.schema_sketch(str(src))

        self._assert_other_threads_ran(
            "write_csv", lambda: jcs.write_csv(str(src), str(out), columns, False, False)
        )

    def test_releasing_the_gil_did_not_change_the_output(self, tmp_path):
        """The fix must be invisible in the result. Guards against someone
        'simplifying' the split between ``write_csv`` and ``write_csv_impl``.

        Uniform rows on purpose: sparse input legitimately round-trips with
        ``None`` in the columns a row lacks (the column union — see
        ``test_sparse_column_union_pass1``), and that behaviour is not what this
        test is about.
        """
        rows = [
            {"id": 1, "n": "a", "meta": {"lane": 1}, "tags": [1, 2]},
            {"id": 2, "n": "b", "meta": {"lane": 2}, "tags": [3]},
        ]
        src, out = tmp_path / "in.jsonl", tmp_path / "out.csv"
        _write_jsonl(src, rows)

        jcs.jsonl_to_csv(str(src), str(out))

        assert _strip_ids(CsvConverter.csv_to_dict(out.read_bytes(), value_kind="array")) == rows
