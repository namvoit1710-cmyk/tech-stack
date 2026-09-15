"""SA-1910 — the native writer must pass the suite the Python writer passes.

`test_split_rust_vs_python.py` compares the two TREES on disk. This compares the
two WRITERS through the control plane's own front door: `store_data` in,
`materialize` out, both flag states, same corpus. If the native path cannot pass
the identity property the Python path already passes, it is not ready — and that
is cheaper to find here than in a shadow soak.

The fallback is tested as a behaviour, not as a comment. A native attempt that
raises must still produce a correct ref, because the alternative is failing a
run whose output is already computed.
"""
from __future__ import annotations

import json

import pytest

from app.layer2_application.data import data_store_service as dss
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient

from ._native_streamer import load

_STREAMER = load()

pytestmark = pytest.mark.skipif(
    not hasattr(_STREAMER, "jsonl_split_recursive"),
    reason="native wheel predates jsonl_split_recursive — rebuild it",
)


CORPUS: list[tuple[str, object]] = [
    ("top_level_table", {"title": "t", "lines": [{"sku": "A", "qty": 1}, {"sku": "B", "qty": 2}]}),
    ("table_under_object", {"order": {"id": 1, "lines": [{"sku": "A"}, {"sku": "B"}]}}),
    ("parent_child", {"orders": [{"id": 1, "items": [{"n": 1}, {"n": 2}]}, {"id": 2, "items": [{"n": 3}]}]}),
    ("three_levels", {"a": [{"id": 1, "b": [{"id": 10, "c": [{"v": 100}, {"v": 101}]}]}]}),
    ("mixed_bag", {"name": "x", "count": 5, "meta": {"w": "W1"}, "tags": ["a", "b"],
                   "rows": [{"r": 1}, {"r": 2}]}),
    ("only_a_table", {"items": [{"n": 1}, {"n": 2}]}),
    ("flat_scalars", {"a": 1, "b": "x", "c": None}),
    ("empty_array_inline", {"e": [], "rows": [{"a": 1}]}),
    ("scalar_array_inline", {"s": [1, 2, 3], "rows": [{"a": 1}]}),
    ("mixed_array_inline", {"m": [{"a": 1}, 7], "rows": [{"a": 1}]}),
    ("unicode", {"rows": [{"u": "àèệ — ✓"}]}),
    ("list_root", [{"id": 1, "kids": [{"k": 1}]}, {"id": 2, "kids": [{"k": 2}]}]),
]


def _store(native: bool) -> DataStoreService:
    return DataStoreService(
        MockFileServiceClient(), CsvConverter(),
        recursive_split=True, native_split=native,
    )


def _canon(x) -> str:
    """Canonical dump, not ``==``: ``1 == 1.0`` and ``True == 1`` in Python, so
    an equality assertion would pass straight over a type divergence between
    two independent CSV writers."""
    return json.dumps(x, sort_keys=True, default=repr)


def _tree(ref: dict, name: str = "") -> dict:
    arts = [a for a in (ref.get("artifacts") or [])
            if a.get("artifact_type") == "nested_array"]
    return {
        "name": name,
        "row_count": ref.get("row_count", 0),
        "columns": sorted(ref.get("columns") or []),
        "value_kind": ref.get("value_kind", ""),
        "item_kind": ref.get("item_kind", ""),
        "children": sorted((_tree(a, a.get("name", "")) for a in arts),
                           key=lambda n: n["name"]),
    }


# ---------------------------------------------------------------------------
# 1. identity, on both writers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True], ids=["python", "native"])
@pytest.mark.parametrize(("name", "data"), CORPUS, ids=[n for n, _ in CORPUS])
async def test_store_then_materialize_is_identity(name, data, native):
    store = _store(native)
    out = await store.materialize(await store.store_data("k", data))
    assert _canon(out) == _canon(data), f"{name} ({'native' if native else 'python'})"


# ---------------------------------------------------------------------------
# 2. the two writers describe the same tree
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "data"), CORPUS, ids=[n for n, _ in CORPUS])
async def test_both_writers_produce_the_same_ref_shape(name, data):
    py = _tree(await _store(False).store_data("k", data))
    rs = _tree(await _store(True).store_data("k", data))
    assert rs == py, (
        f"{name}\npython={json.dumps(py, indent=2)}\nnative={json.dumps(rs, indent=2)}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "data"), CORPUS, ids=[n for n, _ in CORPUS])
async def test_a_value_written_natively_reads_back_through_the_python_reader(name, data):
    """The point of the whole exercise: the readers are unchanged, so a tree the
    crate wrote has to be one `_reconstruct_recursive` already knows."""
    store = _store(True)
    ref = await store.store_data("k", data)
    # a DIFFERENT service instance, native off, sharing the same file service
    reader = DataStoreService(
        store._file_client, CsvConverter(), recursive_split=True, native_split=False,
    )
    assert _canon(await reader.materialize(ref)) == _canon(data)


# ---------------------------------------------------------------------------
# 3. the flag is off by default, and the fallback is real
# ---------------------------------------------------------------------------

def test_the_flag_is_off_by_default():
    assert DataStoreService(MockFileServiceClient(), CsvConverter())._native_split is False


@pytest.mark.asyncio
async def test_a_raising_crate_still_stores_the_value(monkeypatch):
    """A native failure must cost orphaned files, never the output."""
    calls = []

    class _Boom:
        def __getattr__(self, item):
            raise AttributeError(item)

        def jsonl_split_recursive(self, *a, **k):
            calls.append(a)
            raise ValueError("simulated crate failure")

    monkeypatch.setattr(dss, "_STREAMER", _Boom())
    data = {"title": "t", "rows": [{"a": 1}, {"a": 2}]}
    store = _store(True)

    out = await store.materialize(await store.store_data("k", data))

    assert calls, "the native path was never attempted"
    assert _canon(out) == _canon(data)


@pytest.mark.asyncio
async def test_an_old_wheel_without_the_splitter_uses_python(monkeypatch):
    class _Old:
        pass

    monkeypatch.setattr(dss, "_STREAMER", _Old())
    data = {"rows": [{"a": 1}]}
    store = _store(True)
    assert _canon(await store.materialize(await store.store_data("k", data))) == _canon(data)


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True], ids=["python", "native"])
async def test_an_empty_file_id_is_not_caught_by_either_writer(native):
    """A pre-existing weakness, pinned rather than fixed on one side only.

    An upload that answers 2xx with NO `file_id` (the case the file-service
    client already logs as an error) leaves `file_id=""` on the artifact, and
    neither writer notices — `_store_rows_python` and `_ref_from_manifest` have
    the identical line. A transport error still raises, so this is only the
    "success-shaped empty response" path.

    This is pinned on BOTH writers on purpose. Guarding it in the native path
    alone would make the two agree about success and disagree about failure,
    which is the worst of the options: the fallback would re-upload the whole
    tree and hit exactly the same empty response.
    """
    store = _store(native)
    calls = {"n": 0}
    real_upload = store._upload_csv

    async def _flaky(csv_bytes, filename):
        calls["n"] += 1
        if calls["n"] == 2:          # the child
            return ""
        return await real_upload(csv_bytes, filename)

    store._upload_csv = _flaky
    ref = await store.store_data("k", {"title": "t", "rows": [{"a": 1}, {"a": 2}]})

    assert [a["name"] for a in ref["artifacts"]] == ["rows"]
    assert ref["artifacts"][0]["file_id"] == "", (
        "if this now fails, somebody fixed the empty-file_id case — good, but "
        "check the OTHER writer was fixed with it"
    )


@pytest.mark.asyncio
async def test_a_raising_upload_falls_back_and_the_value_survives():
    """The failure mode that IS handled: anything that raises inside the native
    path reaches the Python writer, and the output is not lost."""
    store = _store(True)
    calls = {"n": 0}
    real_upload = store._upload_csv

    async def _flaky(csv_bytes, filename):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("file service refused the child")
        return await real_upload(csv_bytes, filename)

    store._upload_csv = _flaky
    data = {"title": "t", "rows": [{"a": 1}, {"a": 2}]}
    ref = await store.store_data("k", data)

    assert [a["name"] for a in ref["artifacts"]] == ["rows"]
    assert all(a["file_id"] for a in ref["artifacts"])
    assert _canon(await store.materialize(ref)) == _canon(data)


# ---------------------------------------------------------------------------
# 4. the sidecar still rides at the root only
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_arrow_sidecar_is_written_once_at_the_root():
    store = DataStoreService(
        MockFileServiceClient(), CsvConverter(), recursive_split=True,
        native_split=True, arrow_write_enabled=True, arrow_min_bytes=0,
    )
    ref = await store.store_data("k", {"title": "t", "rows": [{"a": 1}, {"a": 2}]})
    assert ref.get("arrow_file_id")
    for art in ref["artifacts"]:
        assert "arrow_file_id" not in art, "a child must not get its own sidecar"


# ---------------------------------------------------------------------------
# 5. the counter — added BEFORE the flag was turned on, on purpose
# ---------------------------------------------------------------------------

class _Monitor:
    def __init__(self):
        self.calls = []

    def increment(self, name, tags=None):
        self.calls.append((name, dict(tags or {})))

    def splits(self):
        return [(t.get("outcome", ""), t.get("cause", ""))
                for n, t in self.calls if n == "native_split_total"]


def _monitored(native: bool, monitor):
    return DataStoreService(
        MockFileServiceClient(), CsvConverter(),
        recursive_split=True, native_split=native, monitor=monitor,
    )


@pytest.mark.asyncio
async def test_a_native_write_is_counted():
    mon = _Monitor()
    await _monitored(True, mon).store_data("k", {"title": "t", "rows": [{"a": 1}]})
    assert mon.splits() == [("native", "")]


@pytest.mark.asyncio
async def test_the_flag_being_off_emits_nothing():
    """Every deployment that has not opted in would otherwise carry a permanent,
    meaningless series."""
    mon = _Monitor()
    await _monitored(False, mon).store_data("k", {"title": "t", "rows": [{"a": 1}]})
    assert mon.splits() == []


@pytest.mark.asyncio
async def test_a_fallback_is_counted_with_the_exception_class():
    """The CLASS, not the message: a message carries file ids and keys, and an
    unbounded metric label is how a metrics backend falls over."""
    mon = _Monitor()
    store = _monitored(True, mon)

    async def _boom(*a, **k):
        raise TimeoutError("file service took too long")

    store._store_rows_native = _boom
    await store.store_data("k", {"title": "t", "rows": [{"a": 1}]})
    assert mon.splits() == [("fallback", "TimeoutError")]


@pytest.mark.asyncio
async def test_a_wheel_without_the_splitter_is_loud(monkeypatch):
    """The flag on but the wheel unable to serve it is a DEPLOYMENT fault. It
    must not read as 'nothing tried the native path' — that is exactly what the
    flag being off looks like."""
    class _Old:
        pass

    monkeypatch.setattr(dss, "_STREAMER", _Old())
    mon = _Monitor()
    await _monitored(True, mon).store_data("k", {"title": "t", "rows": [{"a": 1}]})
    assert mon.splits() == [("fallback", "no_splitter")]


@pytest.mark.asyncio
async def test_a_missing_or_throwing_monitor_cannot_break_a_write():
    store = _monitored(True, None)
    assert await store.store_data("k", {"rows": [{"a": 1}]})

    class _Boom:
        def increment(self, *a, **k):
            raise RuntimeError("statsd down")

    assert await _monitored(True, _Boom()).store_data("k", {"rows": [{"a": 1}]})
