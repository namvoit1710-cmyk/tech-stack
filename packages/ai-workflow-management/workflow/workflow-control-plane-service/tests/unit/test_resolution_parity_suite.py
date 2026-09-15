"""SA-2264 — the never-worse parity suite for variable resolution.

P2-1 makes the lazy row-range read the DEFAULT for flat refs at any size. That
changes the resolution path for MOST traffic, so the regression surface is the
whole product (LAI-27 never-worse). This suite exists before that change, not
after it.

What "old path" and "new path" mean here
----------------------------------------
Both already exist and are selected by one condition in
``FileBackedResolverStrategy.try_resolve``::

    row_index = self._find_first_int_index(path_parts)
    if row_index is not None and self._file_client is not None:
        ...                       # LAZY: one OData row, no download
    ...
    materialized = await self._cached_materialize(source_data)   # EAGER: whole file

So the two paths are exercised today by constructing the strategy with and
without a file client, over the SAME stored bytes. When P2-1 widens the lazy
route, these tests keep asking the same question and simply cover more of it.

Why this shape of test earns its keep
-------------------------------------
This is not a hypothetical risk. ``_try_odata_row_fetch`` carries a comment
recording that the same file, same row, gave THREE readers TWO answers — the
lazy reader returned ``{'__row_id': 2, '__value': 20, '_row_index': 1}`` where
the eager reader returned ``20`` — and the lazy one wins, because the index
fast-path runs first. SA-1977 fixed those two. Nothing stopped the third from
appearing. That is what a parity suite is for: it does not care WHICH answer is
right, only that every reader gives the same one.
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
    """Passthrough that records which read primitive was actually used.

    Without this the suite could pass vacuously: ``_try_odata_row_fetch``
    swallows any failure and returns UNRESOLVED, after which the SAME
    whole-file materialize runs. Both resolvers would then be the eager one,
    agree trivially, and prove nothing. The counters make "the two routes were
    genuinely different" an assertion rather than an assumption.
    """

    def __init__(self, inner) -> None:
        self._inner = inner
        self.odata_calls = 0
        self.download_calls = 0

    async def query_file_data(self, file_id, query_params):
        self.odata_calls += 1
        return await self._inner.query_file_data(file_id, query_params)

    async def download_file(self, file_id):
        self.download_calls += 1
        return await self._inner.download_file(file_id)

    def reset(self) -> None:
        self.odata_calls = 0
        self.download_calls = 0

    def __getattr__(self, item):
        return getattr(self._inner, item)


class _Harness:
    """One stored value, two resolvers reading the SAME bytes.

    ``eager`` has no file client, so every path falls through to the whole-file
    materialize. ``lazy`` has one, so an indexed path takes the OData row read.
    Any difference between them is a real divergence, not a fixture artefact.
    """

    def __init__(self) -> None:
        self.client = _CountingClient(MockFileServiceClient())
        self.store = DataStoreService(self.client, CsvConverter())
        self.eager = FileBackedResolverStrategy(self.store, file_client=None)
        self.lazy = FileBackedResolverStrategy(self.store, file_client=self.client)

    async def put(self, key: str, value):
        return await self.store.store_data(key, value)


@pytest.fixture
def harness():
    token = set_current_run_id("parity-run")
    try:
        yield _Harness()
    finally:
        reset_current_run_id(token)


async def _both(harness: _Harness, ref, path: tuple):
    """Resolve *path* through both routes and return (eager, lazy)."""
    eager = await harness.eager.try_resolve(ref, path)
    lazy = await harness.lazy.try_resolve(ref, path)
    return eager, lazy


def _assert_parity(shape: str, path: tuple, eager, lazy) -> None:
    assert eager == lazy, (
        f"RESOLUTION DIVERGENCE on shape={shape!r} path={path}:\n"
        f"  eager (whole-file materialize) -> {eager!r}\n"
        f"  lazy  (OData row read)         -> {lazy!r}\n"
        "Same file, same path, two answers. Whichever is correct, a caller's "
        "result now depends on which route happened to run."
    )
    assert type(eager) is type(lazy), (
        f"TYPE DIVERGENCE on shape={shape!r} path={path}: "
        f"{type(eager).__name__} vs {type(lazy).__name__} — equal values of "
        "different types still break downstream nodes (e.g. '3' vs 3)."
    )


# ----------------------------------------------------------------------
# The shapes the ticket names: scalar, index path, deep non-index,
# artifact, OData.
# ----------------------------------------------------------------------

LIST_OF_OBJECTS = [
    {"sku": "A1", "qty": 3, "active": True},
    {"sku": "B2", "qty": 0, "active": False},
    {"sku": "C3", "qty": 17, "active": True},
]
LIST_OF_SCALARS = [10, 20, 30]
NESTED_OBJECT = {
    "order": {"id": "o-1", "customer": {"name": "Acme", "tier": 2}},
    "total": 99.5,
}
OBJECT_WITH_ARRAY = {
    "header": "H",
    "items": [{"x": 1, "y": "a"}, {"x": 2, "y": "b"}],
}
SINGLE_ELEMENT = [{"only": "row"}]


class TestIndexedPaths:
    """The shape where the two routes genuinely differ today."""

    @pytest.mark.parametrize("path", [(0,), (1,), (2,)])
    async def test_whole_row_by_index(self, harness, path):
        ref = await harness.put("n", LIST_OF_OBJECTS)
        e, l = await _both(harness, ref, path)
        _assert_parity("list[object]", path, e, l)

    @pytest.mark.parametrize("path", [(0, "sku"), (1, "qty"), (2, "active")])
    async def test_field_under_index(self, harness, path):
        ref = await harness.put("n", LIST_OF_OBJECTS)
        e, l = await _both(harness, ref, path)
        _assert_parity("list[object]", path, e, l)

    @pytest.mark.parametrize("path", [(0,), (1,), (2,)])
    async def test_scalar_list_by_index(self, harness, path):
        """The exact SA-1977 defect: __value envelope vs the bare scalar."""
        ref = await harness.put("n", LIST_OF_SCALARS)
        e, l = await _both(harness, ref, path)
        _assert_parity("list[scalar]", path, e, l)

    async def test_single_element_collection(self, harness):
        """A 1-row file is where 'is it a list?' and 'can it be row-ranged?'
        were conflated — two questions, one wrong answer."""
        ref = await harness.put("n", SINGLE_ELEMENT)
        for path in [(0,), (0, "only")]:
            e, l = await _both(harness, ref, path)
            _assert_parity("single-element", path, e, l)

    async def test_out_of_range_index_agrees(self, harness):
        """Both routes must fail the SAME way, not one empty and one raising."""
        ref = await harness.put("n", LIST_OF_OBJECTS)
        path = (99,)
        e_exc = l_exc = None
        try:
            e = await harness.eager.try_resolve(ref, path)
        except Exception as exc:
            e, e_exc = None, type(exc).__name__
        try:
            l = await harness.lazy.try_resolve(ref, path)
        except Exception as exc:
            l, l_exc = None, type(exc).__name__
        assert e_exc == l_exc, (
            f"out-of-range index raises {e_exc} eagerly but {l_exc} lazily"
        )
        assert e == l


class TestNonIndexedShapes:
    """These take the same route today; the suite pins them so P2-1 cannot
    quietly move one of them onto a different reader."""

    @pytest.mark.parametrize("path", [
        ("total",),
        ("order", "id"),
        ("order", "customer", "name"),
        ("order", "customer", "tier"),
    ])
    async def test_deep_non_index_path(self, harness, path):
        ref = await harness.put("n", NESTED_OBJECT)
        e, l = await _both(harness, ref, path)
        _assert_parity("nested-object", path, e, l)

    @pytest.mark.parametrize("path", [
        ("header",),
        ("items", 0, "x"),
        ("items", 1, "y"),
    ])
    async def test_artifact_backed_array_field(self, harness, path):
        ref = await harness.put("n", OBJECT_WITH_ARRAY)
        e, l = await _both(harness, ref, path)
        _assert_parity("object-with-array", path, e, l)

    async def test_missing_key_agrees(self, harness):
        ref = await harness.put("n", NESTED_OBJECT)
        path = ("nope",)
        e, l = await _both(harness, ref, path)
        assert e is UNRESOLVED and l is UNRESOLVED, (
            f"a missing key resolves differently: eager={e!r} lazy={l!r}"
        )


class TestEmptyAndEdgeShapes:
    @pytest.mark.parametrize("value", [[], {}])
    async def test_empty_values(self, harness, value):
        ref = await harness.put("n", value)
        for path in [("anything",), (0,)]:
            e, l = await _both(harness, ref, path)
            _assert_parity(f"empty:{value!r}", path, e, l)

    async def test_falsy_values_survive_both_routes(self, harness):
        """0, '' and False must not be confused with 'absent' on either route."""
        ref = await harness.put("n", [{"n": 0, "s": "", "b": False}])
        for path in [(0, "n"), (0, "s"), (0, "b")]:
            e, l = await _both(harness, ref, path)
            _assert_parity("falsy-fields", path, e, l)

    async def test_meta_paths_are_route_independent(self, harness):
        ref = await harness.put("n", LIST_OF_OBJECTS)
        for path in [("__row_count",), ("__columns",)]:
            e, l = await _both(harness, ref, path)
            _assert_parity("meta", path, e, l)


class TestTheTwoRoutesAreGenuinelyDifferent:
    """Soundness of the suite itself: if both resolvers took the SAME route,
    every parity assertion above would hold vacuously."""

    async def test_indexed_read_uses_odata_lazily_and_download_eagerly(self, harness):
        ref = await harness.put("n", LIST_OF_OBJECTS)

        harness.client.reset()
        await harness.lazy.try_resolve(ref, (1, "sku"))
        lazy_odata = harness.client.odata_calls
        lazy_downloads = harness.client.download_calls

        harness.client.reset()
        await harness.eager.try_resolve(ref, (1, "sku"))
        eager_odata = harness.client.odata_calls
        eager_downloads = harness.client.download_calls

        assert lazy_odata > 0, (
            "the 'lazy' resolver never called OData — it silently fell through "
            "to the whole-file materialize, so every parity assertion in this "
            "file is comparing the eager route against itself"
        )
        assert lazy_downloads == 0, (
            f"the lazy route still downloaded the whole file "
            f"({lazy_downloads} calls) — it is not lazy"
        )
        assert eager_downloads > 0 and eager_odata == 0, (
            "the 'eager' resolver did not take the whole-file route"
        )

    async def test_deep_non_index_also_diverges_by_route(self, harness):
        """Premise updated, per this test's own instruction.

        It used to assert that only INDEXED paths diverge — "P2-1 widens this,
        and when it does this assertion is the one that must be revisited". It
        has been: a deep non-index path on an OBJECT root is now answered from
        one OData page, because that root's main CSV is a single row and the
        eager route was downloading the main file AND every child table to
        reach the same scalar.

        So the scoping note is gone, and what replaces it is the stronger
        statement — the same soundness check the indexed case gets. The value
        parity for this shape is asserted by ``test_deep_non_index_path``; this
        one proves the two routes it compares are genuinely different, so that
        assertion cannot pass vacuously.
        """
        ref = await harness.put("n", NESTED_OBJECT)
        path = ("order", "customer", "name")

        harness.client.reset()
        await harness.lazy.try_resolve(ref, path)
        lazy_odata, lazy_downloads = (
            harness.client.odata_calls, harness.client.download_calls,
        )

        harness.client.reset()
        await harness.eager.try_resolve(ref, path)
        eager_odata, eager_downloads = (
            harness.client.odata_calls, harness.client.download_calls,
        )

        assert lazy_odata > 0 and lazy_downloads == 0, (
            f"the lazy route did not stay lazy on a deep non-index path "
            f"(odata={lazy_odata}, downloads={lazy_downloads})"
        )
        assert eager_downloads > 0 and eager_odata == 0, (
            "the 'eager' resolver did not take the whole-file route"
        )


class TestTheSuiteItselfWouldCatchADivergence:
    """A parity suite that cannot fail is worse than none — it reads as proof.

    This deliberately breaks one route and asserts the comparison notices.
    """

    async def test_a_planted_divergence_is_detected(self, harness):
        ref = await harness.put("n", LIST_OF_SCALARS)

        original = harness.lazy._traverse

        def _sabotage(data, parts):
            result = original(data, parts)
            return {"__row_id": 0, "__value": result} if not parts else result

        harness.lazy._traverse = _sabotage
        e, l = await _both(harness, ref, (0,))
        with pytest.raises(AssertionError, match="RESOLUTION DIVERGENCE"):
            _assert_parity("planted", (0,), e, l)

    async def test_a_planted_type_divergence_is_detected(self):
        with pytest.raises(AssertionError, match="TYPE DIVERGENCE"):
            _assert_parity("planted", ("q",), 3, 3.0)
