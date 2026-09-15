"""SA-1949 — a file_ref nested under a PLAIN-DICT source must resolve.

An inline-output node (``code`` / ``approval_flow``) skips ``store_data``, so with
worker-side streaming ON its big field comes back as a pointer and the node output
is ``{"result": <file_ref>}`` — a plain dict *carrying* a reference.

``_resolve_through`` (SA-1905 F1) already knew how to descend into a nested
reference, but it sat behind the ``is_file_ref(source)`` gate, which such a source
never passes. F1 was unreachable by construction, the chain fell through to
TraversalResolverStrategy, and it int-indexed the reference dict:

    VariableResolutionError: Expected list at index 0, got dict

Note this survives SA-1951's size ceiling: with streaming ON the worker has
already shrunk the payload to a ~130-byte pointer *before* the control plane sees
it, so the size check passes and the output is (correctly) still kept inline. The
two fixes are complementary, not substitutes.
"""

from __future__ import annotations

import pytest

from app.layer2_application.resolve.file_backed_resolver import (
    FileBackedResolverStrategy,
    _carries_file_ref,
)
from app.layer2_application.resolve.variable_resolver import (
    TraversalResolverStrategy,
    VariableResolutionError,
    VariableResolver,
)
from app.layer2_application.resolve.file_backed_resolver import UNRESOLVED


ROWS = [
    {"sku": "ABC-001", "qty": 12},
    {"sku": "ABC-002", "qty": 7},
]


def _ref(file_id="file_019f"):
    return {
        "__file_ref": True,
        "file_id": file_id,
        "row_count": len(ROWS),
        "columns": ["sku", "qty"],
        "content_type": "text/csv",
        "extracted": {},
    }


class _Store:
    """Materializes the ref's rows — stands in for DataStoreService.

    ``materialize_guarded`` is the SA-1942 size-guarded entry point the resolver
    actually calls; ``materialize`` is kept for the whole-value path.
    """

    def __init__(self):
        self.materialized = 0

    async def materialize(self, ref):
        self.materialized += 1
        return ROWS

    async def materialize_guarded(self, ref, context=""):
        return await self.materialize(ref)


def _strategy():
    return FileBackedResolverStrategy(_Store())


class TestTheBug:
    @pytest.mark.asyncio
    async def test_index_access_through_a_nested_ref_resolves(self):
        """$gen.result[0].sku — the exact live repro."""
        source = {"result": _ref()}
        value = await _strategy().try_resolve(source, ("result", 0, "sku"))
        assert value == "ABC-001"

    @pytest.mark.asyncio
    async def test_key_access_through_a_nested_ref_resolves(self):
        source = {"merge": {"branchA": _ref()}}
        value = await _strategy().try_resolve(source, ("merge", "branchA", 1, "qty"))
        assert value == 7

    @pytest.mark.asyncio
    async def test_the_old_error_can_no_longer_be_raised_for_this_shape(self):
        """End-to-end through the real chain: FileBacked → Metadata → Traversal.

        Before the fix FileBacked bailed, Traversal int-indexed the ref dict, and
        this raised. It must now resolve instead.
        """
        resolver = VariableResolver(strategies=[_strategy(), TraversalResolverStrategy()])
        out = await resolver.resolve("{{$gen.result[0].sku}}", {"gen": {"result": _ref()}})
        assert out == "ABC-001"


class TestNoRegression:
    @pytest.mark.asyncio
    async def test_a_plain_dict_with_no_ref_is_left_to_the_other_strategies(self):
        source = {"result": [{"sku": "PLAIN"}]}
        assert await _strategy().try_resolve(source, ("result", 0, "sku")) is UNRESOLVED

    @pytest.mark.asyncio
    async def test_a_missing_key_still_falls_through_to_the_real_error(self):
        """A genuine authoring mistake must still surface Traversal's message,
        not be swallowed into a silent UNRESOLVED."""
        resolver = VariableResolver(strategies=[_strategy(), TraversalResolverStrategy()])
        with pytest.raises(VariableResolutionError):
            await resolver.resolve("{{$gen.result.nope}}", {"gen": {"result": _ref()}})

    @pytest.mark.asyncio
    async def test_a_top_level_file_ref_is_unaffected(self):
        value = await _strategy().try_resolve(_ref(), (0, "sku"))
        assert value == "ABC-001"

    @pytest.mark.asyncio
    async def test_whole_value_access_is_unchanged(self):
        """Empty path on a plain dict: still UNRESOLVED (no accidental download)."""
        store = _Store()
        strat = FileBackedResolverStrategy(store)
        assert await strat.try_resolve({"result": _ref()}, ()) is UNRESOLVED
        assert store.materialized == 0


class TestScanProbe:
    def test_finds_a_ref_one_and_two_levels_down(self):
        assert _carries_file_ref({"result": _ref()}) is True
        assert _carries_file_ref({"merge": {"branchA": _ref()}}) is True

    def test_says_no_for_plain_data(self):
        assert _carries_file_ref({"result": [{"sku": "A"}]}) is False
        assert _carries_file_ref("just a string") is False
        assert _carries_file_ref(None) is False

    def test_is_bounded_on_a_big_list(self):
        """The probe must not walk a 60k-row list — that is the cost we exist to avoid."""
        big = {"rows": [{"sku": f"S{i}"} for i in range(60_000)]}
        assert _carries_file_ref(big) is False
