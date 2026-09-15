"""SA-2262 (part 2): stop downloading N rows to answer "not found".

Measured before the fix: a string path over a 500-row collection ref fetched
the whole file and then returned UNRESOLVED, emitting a
``cause=flat_full_download`` warning — the dominant contributor to the very
counter gate G6 measures.

Both traversal implementations agree the answer is UNRESOLVED the moment a
string part meets a non-dict (``_traverse`` line 711, ``_resolve_through``'s
final ``else``). So the download cannot change the outcome; it can only cost
memory.

The guard reads the ref's DECLARED ``value_kind`` (SA-1977) and never infers
one. A legacy ref that declares nothing reports UNKNOWN and keeps the old
behaviour exactly.
"""

from __future__ import annotations

import pytest

from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import (
    FileBackedResolverStrategy,
)
from app.layer2_application.resolve.variable_resolver import (
    UNRESOLVED,
    reset_current_run_id,
    set_current_run_id,
)
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)


class _CountingClient:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.downloads = 0
        self.odata = 0

    async def download_file(self, file_id):
        self.downloads += 1
        return await self._inner.download_file(file_id)

    async def query_file_data(self, file_id, params):
        self.odata += 1
        return await self._inner.query_file_data(file_id, params)

    def reset(self):
        self.downloads = 0
        self.odata = 0

    def __getattr__(self, item):
        return getattr(self._inner, item)


@pytest.fixture
def env():
    client = _CountingClient(MockFileServiceClient())
    store = DataStoreService(client, CsvConverter())
    strategy = FileBackedResolverStrategy(store, file_client=client)
    token = set_current_run_id("no-download-run")
    try:
        yield client, store, strategy
    finally:
        reset_current_run_id(token)


ROWS = [{"sku": f"S{i}", "qty": i} for i in range(500)]


class TestNoDownloadForAnUnanswerablePath:
    async def test_string_path_over_a_collection_downloads_nothing(self, env):
        client, store, strategy = env
        ref = await store.store_data("n", ROWS)
        client.reset()

        result = await strategy.try_resolve(ref, ("sku",))

        assert result is UNRESOLVED
        assert client.downloads == 0, (
            f"{client.downloads} whole-file download(s) to compute 'not found' "
            "over a declared array — this is the dominant flat_full_download "
            "cause and it changes no answer"
        )
        assert client.odata == 0

    async def test_deep_string_path_over_a_collection_downloads_nothing(self, env):
        client, store, strategy = env
        ref = await store.store_data("n", ROWS)
        client.reset()
        assert await strategy.try_resolve(ref, ("a", "b", "c")) is UNRESOLVED
        assert client.downloads == 0

    async def test_indexed_paths_still_work(self, env):
        """Never-worse: the guard must not touch the shape that DOES resolve."""
        client, store, strategy = env
        ref = await store.store_data("n", ROWS)
        client.reset()
        assert await strategy.try_resolve(ref, (3, "sku")) == "S3"
        assert client.downloads == 0, "the indexed read stopped being lazy"
        assert client.odata > 0

    async def test_object_root_string_paths_are_untouched(self, env):
        """An object root CAN answer a string key — it must still download."""
        client, store, strategy = env
        ref = await store.store_data("n", {"header": "H", "n": 7})
        client.reset()
        assert await strategy.try_resolve(ref, ("header",)) == "H"

    async def test_meta_paths_still_work(self, env):
        client, store, strategy = env
        ref = await store.store_data("n", ROWS)
        client.reset()
        assert await strategy.try_resolve(ref, ("__row_count",)) == 500
        assert client.downloads == 0


class TestTheGuardTrustsTheDeclarationOnly:
    async def test_a_legacy_ref_without_a_declared_kind_is_unchanged(self, env):
        """No value_kind ⇒ UNKNOWN ⇒ old path. Never guess."""
        client, store, strategy = env
        ref = await store.store_data("n", ROWS)
        ref.pop("value_kind", None)
        client.reset()

        result = await strategy.try_resolve(ref, ("sku",))

        assert result is UNRESOLVED
        assert client.downloads == 1, (
            "a ref that declares NO kind took the short-circuit — the guard is "
            "inferring a kind instead of reading one, which is the SA-1977 "
            "defect all over again"
        )

    async def test_an_object_declaration_does_not_short_circuit(self, env):
        client, store, strategy = env
        ref = await store.store_data("n", {"a": 1})
        assert ref.get("value_kind") == "object"
        client.reset()
        assert await strategy.try_resolve(ref, ("a",)) == 1

    async def test_the_declaration_is_what_is_read(self, env):
        """A ref mislabelled as an array short-circuits — proving the guard
        reads the declaration rather than the data."""
        client, store, strategy = env
        ref = await store.store_data("n", {"a": 1})
        ref["value_kind"] = "array"
        client.reset()
        assert await strategy.try_resolve(ref, ("a",)) is UNRESOLVED
        assert client.downloads == 0
