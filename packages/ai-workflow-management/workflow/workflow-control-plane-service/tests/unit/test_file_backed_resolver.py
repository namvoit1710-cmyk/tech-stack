"""Tests for FileBackedResolverStrategy — cache hit, miss, passthrough."""

import pytest

from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer1_domain.entities.data_reference import build_file_ref, is_file_ref
from app.layer2_application.resolve.variable_resolver import (
    set_current_run_id,
    reset_current_run_id,
    VariableResolutionError,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


class _FailedDownloadStore:
    """Stand-in DataStoreService whose ``materialize`` returns ``{}`` —
    simulates ``download_file`` returning None (transient/auth/missing), which
    ``DataStoreService.materialize`` masks as empty."""

    _file_client = None

    async def materialize(self, ref, max_bytes=0):
        return {}

    async def materialize_guarded(self, ref, *, max_bytes=None, context=""):
        # Mirror the real contract: guard-off / unprobeable → plain materialize.
        return await self.materialize(ref)


class TestMaterializeFailureSurfacing:
    """A failed download must surface as a clear error, not the misleading
    'Key X not found. Available keys: [__file_ref, file_id, …]' that results
    from the chain falling through to traversing the raw file_ref envelope."""

    async def test_failed_materialize_raises_clear_error(self):
        strat = FileBackedResolverStrategy(_FailedDownloadStore())
        ref = build_file_ref(file_id="f-1", row_count=1, columns=["templateId"])
        with pytest.raises(VariableResolutionError) as ei:
            await strat.try_resolve(ref, ("templateId",))
        msg = str(ei.value)
        assert "f-1" in msg
        assert "download" in msg.lower()
        # Must NOT leak the internal envelope keys as if they were data.
        assert "__file_ref" not in msg

    async def test_genuinely_empty_ref_returns_unresolved(self):
        """A truly empty ref (no rows/columns/artifacts) is not a failure —
        still UNRESOLVED so optional/empty data resolves as before."""
        strat = FileBackedResolverStrategy(_FailedDownloadStore())
        ref = build_file_ref(file_id="f-empty", row_count=0, columns=[])
        result = await strat.try_resolve(ref, ("templateId",))
        assert not result  # UNRESOLVED (falsy)


@pytest.fixture
def mock_file_client():
    return MockFileServiceClient()


@pytest.fixture
def data_store(mock_file_client):
    return DataStoreService(mock_file_client, CsvConverter())


@pytest.fixture
def strategy(data_store):
    return FileBackedResolverStrategy(data_store)


class TestFileBackedResolverStrategy:
    async def test_non_file_ref_returns_unresolved(self, strategy):
        """Should pass through non-file-ref data to the next strategy."""
        result = await strategy.try_resolve({"key": "value"}, ("key",))
        assert not result  # UNRESOLVED is falsy

    async def test_resolve_from_extracted_cache(self, strategy, data_store):
        """Should resolve from the extracted cache without file download."""
        data = {"output": {"result": 42}, "metadata": {"type": "compute"}}
        ref = await data_store.store_data("node1", data)

        result = await strategy.try_resolve(ref, ("output", "result"))
        assert result == 42

    async def test_resolve_top_level_scalar(self, strategy, data_store):
        """Top-level keys should be in extracted cache."""
        data = {"status_code": 200, "response_body": "ok"}
        ref = await data_store.store_data("task1", data)

        result = await strategy.try_resolve(ref, ("status_code",))
        assert result == 200

        result = await strategy.try_resolve(ref, ("response_body",))
        assert result == "ok"

    async def test_cache_miss_materializes(self, strategy, data_store):
        """On cache miss, should download CSV and resolve."""
        data = {"output": {"nested": {"deep": "value"}}, "top": "level"}
        ref = await data_store.store_data("node1", data)

        # "output.nested.deep" won't be in extracted (only 2 levels cached)
        # but "output.nested" will have the dict
        result = await strategy.try_resolve(ref, ("output", "nested"))
        assert isinstance(result, dict)
        assert result["deep"] == "value"

    async def test_empty_path_materializes_full_data(self, strategy, data_store):
        """Empty path should return the full materialized data."""
        data = {"name": "Alice", "age": 30}
        ref = await data_store.store_data("node1", data)

        result = await strategy.try_resolve(ref, ())
        assert result["name"] == "Alice"
        assert result["age"] == 30

    async def test_list_data(self, strategy, data_store):
        """File refs to list data should materialize correctly."""
        data = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
        ref = await data_store.store_data("items", data)

        result = await strategy.try_resolve(ref, ())
        assert isinstance(result, list)
        assert len(result) == 2

    async def test_cache_updated_after_materialize(self, strategy, data_store):
        """After a cache miss + materialize, the extracted cache should be updated."""
        data = {"output": {"result": 99}}
        ref = await data_store.store_data("node1", data)

        # Simulate a path not initially in cache by clearing extracted
        ref["extracted"] = {}

        result = await strategy.try_resolve(ref, ("output", "result"))
        assert result == 99

        # Cache should now be populated
        assert ref["extracted"]["output.result"]["value"] == 99

    async def test_missing_path_returns_unresolved(self, strategy, data_store):
        """Non-existent paths should return UNRESOLVED after materialize."""
        data = {"name": "Alice"}
        ref = await data_store.store_data("node1", data)
        ref["extracted"] = {}  # Force cache miss

        result = await strategy.try_resolve(ref, ("nonexistent",))
        assert not result  # UNRESOLVED

    async def test_index_access(self, strategy, data_store):
        """Should handle integer index access in path."""
        data = {"items": [10, 20, 30]}
        ref = await data_store.store_data("node1", data)

        # items is a list, stored as JSON string in extracted
        result = await strategy.try_resolve(ref, ("items",))
        assert result == [10, 20, 30]


# ────────────── Phase 1: run-scoped materialize cache ──────────────


class _CountingFileServiceClient(MockFileServiceClient):
    """MockFileServiceClient that counts download_file / query_file_data calls."""

    def __init__(self) -> None:
        super().__init__()
        self.download_calls: int = 0
        self.query_calls: int = 0

    async def download_file(self, file_id: str):
        self.download_calls += 1
        return await super().download_file(file_id)

    async def query_file_data(self, file_id: str, query_params: dict):
        self.query_calls += 1
        return await super().query_file_data(file_id, query_params)


def _fresh_ref(initial: dict) -> dict:
    """Build a *new* file_ref dict pointing at the same file_id but with an
    empty ``extracted`` cache. This mirrors the production case where
    different runs (or different node contexts) get independent copies of
    the file-ref from the materialised projection.  Using a fresh ref on
    each resolve forces the code path through ``_cached_materialize``
    instead of short-circuiting on ``extracted``.
    """
    return build_file_ref(
        file_id=initial["file_id"],
        row_count=initial.get("row_count", 0),
        columns=list(initial.get("columns", [])),
        content_type=initial.get("content_type", "text/csv"),
    )


class TestRunScopedMaterializeCache:
    async def test_two_resolves_in_same_run_download_once(self):
        """Within a run, two expressions resolving via full materialize on the
        same file_id should hit the file service exactly once."""
        client = _CountingFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        initial = await store.store_data("nodeX", {"a": {"deep": "value"}})
        client.download_calls = 0

        token = set_current_run_id("run-1")
        try:
            r1 = await strategy.try_resolve(_fresh_ref(initial), ("a",))
            r2 = await strategy.try_resolve(_fresh_ref(initial), ("a",))
        finally:
            reset_current_run_id(token)

        assert r1 == r2 == {"deep": "value"}
        assert client.download_calls == 1, (
            f"Expected one download within the run, saw {client.download_calls}"
        )

    async def test_different_runs_have_isolated_caches(self):
        """Two different runs against the same file_id each trigger a download."""
        client = _CountingFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        initial = await store.store_data("nodeX", {"a": {"deep": "value"}})
        client.download_calls = 0

        token1 = set_current_run_id("run-A")
        try:
            await strategy.try_resolve(_fresh_ref(initial), ("a",))
        finally:
            reset_current_run_id(token1)

        token2 = set_current_run_id("run-B")
        try:
            await strategy.try_resolve(_fresh_ref(initial), ("a",))
        finally:
            reset_current_run_id(token2)

        assert client.download_calls == 2

    async def test_drop_run_cache_invalidates_subsequent_lookup(self):
        """After drop_run_cache, the next resolve re-downloads."""
        client = _CountingFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        initial = await store.store_data("nodeX", {"a": {"deep": "value"}})
        client.download_calls = 0

        token = set_current_run_id("run-1")
        try:
            await strategy.try_resolve(_fresh_ref(initial), ("a",))
            assert client.download_calls == 1

            strategy.drop_run_cache("run-1")

            await strategy.try_resolve(_fresh_ref(initial), ("a",))
            assert client.download_calls == 2
        finally:
            reset_current_run_id(token)

    async def test_no_run_id_falls_back_to_shared_bucket(self):
        """Calls made without a run_id set still cache and still honour the
        legacy ``clear_materialize_cache()`` helper."""
        client = _CountingFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        initial = await store.store_data("nodeX", {"a": {"deep": "value"}})
        client.download_calls = 0

        # No contextvar set — falls back to the None bucket.
        await strategy.try_resolve(_fresh_ref(initial), ("a",))
        await strategy.try_resolve(_fresh_ref(initial), ("a",))
        assert client.download_calls == 1

        # Legacy wipe clears only the shared (None) bucket.
        strategy.clear_materialize_cache()

        await strategy.try_resolve(_fresh_ref(initial), ("a",))
        assert client.download_calls == 2


class TestRunScopedODataCache:
    async def test_two_odata_fetches_in_same_run_query_once(self):
        """Within a run, two expressions hitting the same file_ref[index]
        should call the file service's OData endpoint exactly once."""
        client = _CountingFileServiceClient()
        # Seed a multi-row CSV through the mock store so OData returns data.
        await client.upload_file(
            b"id,name\n1,Alice\n2,Bob\n3,Carol\n",
            "rows.csv",
            "text/csv",
        )
        file_id = list(client._store.keys())[0]

        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        ref = build_file_ref(
            file_id=file_id,
            row_count=3,
            columns=["id", "name"],
            content_type="text/csv",
        )

        client.query_calls = 0
        token = set_current_run_id("run-1")
        try:
            v1 = await strategy.try_resolve(ref, (1, "name"))
            v2 = await strategy.try_resolve(ref, (1, "name"))
        finally:
            reset_current_run_id(token)

        assert v1 == v2 == "Bob"
        assert client.query_calls == 1, (
            f"Expected one OData query within the run, saw {client.query_calls}"
        )


class TestSchemaTypingThroughResolver:
    """The resolver threads the file ref's ``column_types`` into every read
    path, so ID-like strings survive both dotted access and indexed (OData)
    single-row access — the same guarantee ``materialize`` gives."""

    async def test_dotted_access_preserves_id_string(self):
        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        ref = await store.store_data("node1", {"code": "0001", "qty": 5})
        token = set_current_run_id("run-1")
        try:
            assert await strategy.try_resolve(ref, ("code",)) == "0001"
            assert await strategy.try_resolve(ref, ("qty",)) == 5
        finally:
            reset_current_run_id(token)

    async def test_indexed_artifact_odata_preserves_id_string(self):
        # ``rows[0].code`` routes through the artifact OData single-row fetch,
        # which must cast via the artifact's stored column_types.
        client = MockFileServiceClient()
        store = DataStoreService(client, CsvConverter())
        strategy = FileBackedResolverStrategy(store, file_client=client)

        ref = await store.store_data(
            "node2", {"status": "ok", "rows": [{"code": "0007"}, {"code": "0042"}]}
        )
        token = set_current_run_id("run-1")
        try:
            assert await strategy.try_resolve(ref, ("rows", 0, "code")) == "0007"
            assert await strategy.try_resolve(ref, ("rows", 1, "code")) == "0042"
        finally:
            reset_current_run_id(token)
