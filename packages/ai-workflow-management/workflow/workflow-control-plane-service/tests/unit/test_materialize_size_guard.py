"""SA-1942 — size-guarded materialize (read-side OOM safety).

The variable resolver's deep-path fallback, the child-workflow output relay,
and HITL resume all ``materialize`` a file_ref — a FULL download into RAM. A
large output (100 MB–1 GB) OOMs the ~1 GB control-plane pod. These tests cover
``DataStoreService.probe_size_bytes`` / ``materialize_guarded`` (the guard),
its post-download backstop, and the resolver + child-relay wiring.
"""

import pytest

from app.layer2_application.data.data_store_service import (
    DataStoreService,
    MaterializeTooLargeError,
)
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.resolve.variable_resolver import (
    VariableResolutionError,
    set_current_run_id,
    reset_current_run_id,
)
from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref, is_file_ref
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


class _CountingClient(MockFileServiceClient):
    """Counts download / head / query calls so a test can prove the guard
    refused a value WITHOUT downloading it."""

    def __init__(self) -> None:
        super().__init__()
        self.download_calls = 0
        self.head_calls = 0
        self.query_calls = 0

    async def download_file(self, file_id):
        self.download_calls += 1
        return await super().download_file(file_id)

    async def head_file(self, file_id):
        self.head_calls += 1
        return await super().head_file(file_id)

    async def query_file_data(self, file_id, query_params):
        self.query_calls += 1
        return await super().query_file_data(file_id, query_params)


class _NoHeadClient(_CountingClient):
    """File service that can't report size (no HEAD route) — head_file → None.
    Exercises the post-download backstop path."""

    async def head_file(self, file_id):
        self.head_calls += 1
        return None


# ─────────────────────────── probe_size_bytes ───────────────────────────


class TestProbeSizeBytes:
    async def test_probe_reports_main_csv_bytes(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter())
        ref = await store.store_data("n1", {"name": "Alice", "age": 30})

        size = await store.probe_size_bytes(ref)
        # The mock HEAD returns len(stored CSV bytes); must be a positive int.
        raw = await client.download_file(ref["file_id"])
        assert size == len(raw)

    async def test_probe_sums_artifacts(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter())
        # An array-of-objects field is split into an array_field artifact whose
        # size_bytes is stamped on the ref — probe must ADD it to the main CSV.
        ref = await store.store_data(
            "n2", {"title": "t", "rows": [{"a": 1}, {"a": 2}, {"a": 3}]},
        )
        assert ref.get("artifacts"), "expected an array_field artifact"
        size = await store.probe_size_bytes(ref)
        main = len(await client.download_file(ref["file_id"]))
        art_total = sum(int(a.get("size_bytes") or 0) for a in ref["artifacts"])
        assert size == main + art_total
        assert art_total > 0

    async def test_probe_head_probes_artifact_without_declared_size(self):
        """An artifact WITHOUT a stamped size_bytes is HEAD-probed; a ref whose
        weight lives entirely in artifacts (empty main file_id — the loop-chunk
        manifest shape) is still measured."""
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter())
        await client.upload_file(b"x,y\n1,2\n3,4\n5,6\n", "art.csv", "text/csv")
        art_id = list(client._store.keys())[0]
        ref = build_file_ref(file_id="", row_count=0)  # empty main
        add_artifact(ref, file_id=art_id, name="a", size_bytes=0)  # undeclared size

        size = await store.probe_size_bytes(ref)
        art_bytes = len(await client.download_file(art_id))
        assert size == art_bytes  # main skipped (no id) + HEAD-probed artifact

    async def test_probe_empty_main_no_artifacts_is_none(self):
        store = DataStoreService(_CountingClient(), CsvConverter())
        ref = build_file_ref(file_id="", row_count=0)
        assert await store.probe_size_bytes(ref) is None

    async def test_probe_returns_none_when_unprobeable(self):
        client = _NoHeadClient()
        store = DataStoreService(client, CsvConverter())
        ref = await store.store_data("n3", {"x": 1})
        # A ref with NO artifact size_bytes and a HEAD that returns None → None.
        assert await store.probe_size_bytes(ref) is None

    async def test_probe_non_file_ref_is_none(self):
        store = DataStoreService(_CountingClient(), CsvConverter())
        assert await store.probe_size_bytes({"not": "a ref"}) is None
        assert await store.probe_size_bytes("plain") is None


# ─────────────────────────── materialize_guarded ─────────────────────────


class TestMaterializeGuarded:
    async def test_under_cap_materializes(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=10_000_000)
        ref = await store.store_data("n1", {"name": "Alice", "age": 30})

        result = await store.materialize_guarded(ref)
        assert result["name"] == "Alice"
        assert result["age"] == 30

    async def test_over_cap_raises_without_downloading(self):
        client = _CountingClient()
        # Cap 1 byte — any real content exceeds it.
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=1)
        ref = await store.store_data("n1", {"blob": "x" * 5000})
        client.download_calls = 0

        with pytest.raises(MaterializeTooLargeError) as ei:
            await store.materialize_guarded(ref, context="node.output")
        # Guard fired on the HEAD probe → the value was NEVER downloaded.
        assert client.download_calls == 0
        exc = ei.value
        assert exc.max_bytes == 1
        assert exc.size_bytes > 1
        assert "node.output" in str(exc)
        assert "OOM guard" in str(exc)

    async def test_cap_zero_is_passthrough_no_probe(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=0)
        ref = await store.store_data("n1", {"a": 1})
        client.head_calls = 0

        result = await store.materialize_guarded(ref)
        assert result == {"a": 1}
        # Guard disabled → no HEAD probe overhead.
        assert client.head_calls == 0

    async def test_explicit_max_bytes_overrides_instance_cap(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=0)
        ref = await store.store_data("n1", {"blob": "y" * 5000})
        with pytest.raises(MaterializeTooLargeError):
            await store.materialize_guarded(ref, max_bytes=1)

    async def test_non_file_ref_passthrough(self):
        store = DataStoreService(_CountingClient(), CsvConverter(), materialize_max_bytes=1)
        assert await store.materialize_guarded({"plain": "dict"}) == {"plain": "dict"}

    async def test_post_download_backstop_when_head_missing(self):
        """HEAD unavailable → the guard can't pre-check, but the post-download
        byte backstop still raises BEFORE the (blow-up) parse — AND preserves the
        caller's context so the actionable message survives the fallback."""
        client = _NoHeadClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=100)
        ref = await store.store_data("n1", {"blob": "z" * 5000})
        client.download_calls = 0

        with pytest.raises(MaterializeTooLargeError) as ei:
            await store.materialize_guarded(ref, context="deep.path")
        # It DID download (HEAD gave nothing), but raised on the raw bytes
        # before csv_to_dict/json.loads could blow RAM up 5.5–9.3×.
        assert client.download_calls == 1
        assert ei.value.context == "deep.path"   # caller context threaded through

    async def test_no_head_under_cap_materializes(self):
        """NoHead client, bytes under cap → the backstop lets it through."""
        client = _NoHeadClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=10_000_000)
        ref = await store.store_data("n1", {"a": 1})
        assert await store.materialize_guarded(ref) == {"a": 1}

    async def test_boundary_size_equals_cap_materializes(self):
        """Strict ``>``: a value whose size EXACTLY equals the cap materializes
        (does not raise) — on both the HEAD pre-check and the backstop."""
        client = _CountingClient()
        probe_store = DataStoreService(client, CsvConverter())
        ref = await probe_store.store_data("n1", {"a": 1, "b": 2})
        size = await probe_store.probe_size_bytes(ref)
        assert size and size > 0

        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=size)
        assert await store.materialize_guarded(ref) == {"a": 1, "b": 2}  # HEAD ==cap

        nohead = _NoHeadClient()
        ph2 = DataStoreService(nohead, CsvConverter())
        ref2 = await ph2.store_data("n1", {"a": 1, "b": 2})
        raw = await nohead.download_file(ref2["file_id"])
        store2 = DataStoreService(nohead, CsvConverter(), materialize_max_bytes=len(raw))
        assert await store2.materialize_guarded(ref2) == {"a": 1, "b": 2}  # backstop ==cap

    async def test_materialize_default_has_no_cap(self):
        """Plain materialize (no max_bytes) never guards — every existing caller
        keeps its behaviour."""
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter())
        ref = await store.store_data("n1", {"blob": "q" * 5000})
        result = await store.materialize(ref)  # no max_bytes
        assert result["blob"] == "q" * 5000


# ───────────────── manifest / multi-file budget (HIGH-1 fix) ─────────────


async def _seed_loop_chunk_manifest(client, n_chunks, rows_per_chunk):
    """Build a loop-results manifest: empty main file_id, N ``loop_chunk``
    artifacts (each a real CSV in the mock store) — the shape whose chunk
    downloads bypass the main-CSV backstop."""
    ref = build_file_ref(file_id="")
    for i in range(n_chunks):
        body = b"col\n" + (b"v\n" * rows_per_chunk)
        await client.upload_file(body, f"chunk{i}.csv", "text/csv")
    for cid in list(client._store.keys()):
        add_artifact(ref, file_id=cid, name="loop", artifact_type="loop_chunk")
    return ref


class TestManifestBudget:
    async def test_loop_chunk_over_cap_raises_via_backstop_no_head(self):
        """HIGH-1: even with HEAD unavailable, the cumulative chunk download is
        capped — the manifest can't OOM by concatenating chunks past the cap."""
        client = _NoHeadClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=50)
        ref = await _seed_loop_chunk_manifest(client, n_chunks=5, rows_per_chunk=40)

        with pytest.raises(MaterializeTooLargeError):
            await store.materialize_guarded(ref, context="loop.results")

    async def test_loop_chunk_over_cap_caught_by_probe_no_download(self):
        """With a working HEAD, the probe measures the chunk artifacts and the
        manifest is refused BEFORE any chunk is downloaded."""
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=50)
        ref = await _seed_loop_chunk_manifest(client, n_chunks=5, rows_per_chunk=40)
        client.download_calls = 0

        with pytest.raises(MaterializeTooLargeError):
            await store.materialize_guarded(ref)
        assert client.download_calls == 0   # probe HEAD-summed the chunks

    async def test_loop_chunk_under_cap_materializes(self):
        client = _NoHeadClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=10_000_000)
        ref = await _seed_loop_chunk_manifest(client, n_chunks=3, rows_per_chunk=5)
        rows = await store.materialize_guarded(ref)
        assert isinstance(rows, list) and len(rows) == 15  # 3 chunks × 5 rows

    async def test_array_field_reattach_over_cap_propagates_not_swallowed(self):
        """The array_field re-attach download is inside a broad ``except`` that
        logs 'failed to materialize'. The OOM guard must PROPAGATE, not be masked
        as a per-artifact warning."""
        client = _NoHeadClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=80)
        # main dict + a big array-of-objects field → split into an array_field
        # artifact; main + artifact cumulative bytes exceed the cap.
        big_store = DataStoreService(client, CsvConverter())
        ref = await big_store.store_data(
            "n", {"title": "t", "rows": [{"a": i, "b": "x" * 10} for i in range(20)]},
        )
        assert any(a.get("artifact_type") == "array_field" for a in ref["artifacts"])

        with pytest.raises(MaterializeTooLargeError):
            await store.materialize_guarded(ref)


# ─────────────────────────── resolver wiring ─────────────────────────────


class TestResolverGuard:
    async def test_deep_path_over_cap_raises_variable_resolution_error(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=1)
        strat = FileBackedResolverStrategy(store, file_client=client)
        # A deep NON-index path forces the _cached_materialize fallback.
        ref = await store.store_data("n1", {"output": {"nested": {"deep": "v" * 3000}}})
        ref["extracted"] = {}  # force cache miss → deep-path materialize
        client.download_calls = 0

        token = set_current_run_id("run-guard")
        try:
            with pytest.raises(VariableResolutionError) as ei:
                await strat.try_resolve(ref, ("output", "nested"))
        finally:
            reset_current_run_id(token)
        msg = str(ei.value)
        assert "output.nested" in msg           # the path is named
        assert "OOM guard" in msg or "materialize cap" in msg
        assert client.download_calls == 0        # never downloaded
        assert isinstance(ei.value.__cause__, MaterializeTooLargeError)  # `from exc`

    async def test_deep_path_under_cap_resolves(self):
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=10_000_000)
        strat = FileBackedResolverStrategy(store, file_client=client)
        ref = await store.store_data("n1", {"output": {"nested": {"deep": "value"}}})
        ref["extracted"] = {}

        token = set_current_run_id("run-ok")
        try:
            result = await strat.try_resolve(ref, ("output", "nested"))
        finally:
            reset_current_run_id(token)
        assert result == {"deep": "value"}

    async def test_empty_path_over_cap_raises(self):
        """The (defensive) whole-value empty-path materialize is also guarded."""
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=1)
        strat = FileBackedResolverStrategy(store, file_client=client)
        ref = await store.store_data("n1", {"blob": "w" * 5000})
        client.download_calls = 0

        with pytest.raises(VariableResolutionError):
            await strat.try_resolve(ref, ())
        assert client.download_calls == 0

    async def test_cache_hit_skips_probe(self):
        """A value that materialized under the cap once is cached; a second
        resolve in the same run must NOT re-probe or re-download."""
        client = _CountingClient()
        store = DataStoreService(client, CsvConverter(), materialize_max_bytes=10_000_000)
        strat = FileBackedResolverStrategy(store, file_client=client)
        ref1 = await store.store_data("n1", {"output": {"a": {"deep": "x"}}})

        def _fresh():
            return build_file_ref(
                file_id=ref1["file_id"], row_count=ref1.get("row_count", 0),
                columns=list(ref1.get("columns", [])),
            )

        token = set_current_run_id("run-cache")
        try:
            await strat.try_resolve(_fresh(), ("output", "a"))
            client.head_calls = 0
            client.download_calls = 0
            await strat.try_resolve(_fresh(), ("output", "a"))
        finally:
            reset_current_run_id(token)
        assert client.download_calls == 0   # served from run cache
        assert client.head_calls == 0       # no re-probe on a cache hit
