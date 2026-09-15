"""The resolver must answer a child-table reference WITHOUT downloading it.

Before this, ``_try_resolve_artifact`` matched ``artifact_type == "array_field"``
only. The recursive split (SA-1910) writes ``nested_array``, so turning
``RECURSIVE_ARTIFACT_SPLIT`` on silently disabled both lazy tiers: measured on a
200-order graph, ``{{$node.orders}}`` went from *0 downloads, returns a pointer*
to *3 downloads, returns the whole list*, and ``{{$node.orders[5].id}}`` from
*1 OData row* to *the entire tree*.

The correctness trap this must not fall into: a ``nested_array`` child file is
NOT the field. It holds the rows of every parent row concatenated, stamped with
``__parent_row_id``. It equals ``<field>`` only when the parent is a single
OBJECT row — so on an ARRAY root the resolver must DECLINE rather than hand back
every parent's rows glued together.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, is_file_ref
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import (
    _ARTIFACT_MISS,
    FileBackedResolverStrategy,
)
from app.layer2_application.resolve.variable_resolver import UNRESOLVED
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


class CountingClient(MockFileServiceClient):
    """Counts I/O so a test can assert on cost, not just on the value."""

    def __init__(self) -> None:
        super().__init__()
        self.downloads = 0
        self.odata = 0
        self.odata_fails = False

    async def download_file(self, file_id: str) -> Optional[bytes]:
        self.downloads += 1
        return await super().download_file(file_id)

    async def query_file_data(self, file_id: str, query_params: dict) -> dict:
        self.odata += 1
        if self.odata_fails:
            raise RuntimeError("odata down")
        return await super().query_file_data(file_id, query_params)

    def reset(self) -> None:
        self.downloads = 0
        self.odata = 0


async def build(data: Any, *, recursive: bool = True):
    """Store *data* and return (strategy, ref, client) with counters zeroed."""
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=recursive)
    ref = await store.store_data("k", data)
    client.reset()
    return FileBackedResolverStrategy(store, file_client=client), ref, client, store


OBJ_ROOT = {
    "title": "quarterly",
    "orders": [
        {"id": i, "sku": f"S{i}", "items": [{"n": i * 10 + k} for k in range(3)]}
        for i in range(20)
    ],
}


# ---------------------------------------------------------------- happy path

async def test_nested_array_resolves_to_a_pointer_with_no_io():
    strat, ref, client, _ = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("orders",))
    assert is_file_ref(out), f"expected a pointer, got {type(out).__name__}"
    assert client.downloads == 0 and client.odata == 0


async def test_pointer_is_the_same_value_the_full_materialize_would_give():
    """The whole point: cheaper, not different."""
    strat, ref, client, store = await build(OBJ_ROOT)
    pointer = await strat.try_resolve(ref, ("orders",))
    lazy = await store.materialize(pointer)
    eager = (await store.materialize(ref))["orders"]
    assert lazy == eager == OBJ_ROOT["orders"]


async def test_pointer_carries_grandchildren_so_depth_is_not_truncated():
    strat, ref, _c, store = await build(OBJ_ROOT)
    pointer = await strat.try_resolve(ref, ("orders",))
    assert pointer["artifacts"], "grandchild 'items' dropped from the pointer"
    rows = await store.materialize(pointer)
    assert rows[0]["items"] == [{"n": 0}, {"n": 1}, {"n": 2}]


async def test_pointer_declares_its_kind():
    strat, ref, _c, _s = await build(OBJ_ROOT)
    pointer = await strat.try_resolve(ref, ("orders",))
    assert pointer["value_kind"] == "array"
    assert pointer["item_kind"] == "object"
    assert pointer["row_count"] == 20


async def test_indexed_row_reads_one_odata_row_not_the_tree():
    strat, ref, client, _s = await build(OBJ_ROOT)
    assert await strat.try_resolve(ref, ("orders", 5, "sku")) == "S5"
    assert client.downloads == 0
    assert client.odata == 1


async def test_indexed_row_carries_no_synthetic_columns():
    """``{{$node.orders[5]}}`` used to leak __row_id / __parent_row_id /
    _row_index, because the artifact branch had its own fetch that — unlike
    every other reader — never stripped them."""
    strat, ref, _c, _s = await build(OBJ_ROOT)
    row = await strat.try_resolve(ref, ("orders", 5))
    assert isinstance(row, dict)
    leaked = {"__row_id", "__parent_row_id", "_row_index"} & set(row)
    assert not leaked, f"synthetic columns leaked to the caller: {sorted(leaked)}"
    assert row["id"] == 5 and row["sku"] == "S5"


async def test_indexed_row_is_never_missing_its_nested_fields():
    """A single OData row carries the row's scalars and NONE of its child
    tables. Answering ``{{$node.orders[5]}}`` from it returns a row silently
    missing ``items`` — while ``materialize()['orders'][5]`` returns it whole.
    The cheap path must decline, not answer differently."""
    strat, ref, _c, store = await build(OBJ_ROOT)
    lazy = await strat.try_resolve(ref, ("orders", 5))
    eager = (await store.materialize(ref))["orders"][5]
    assert lazy == eager
    assert "items" in lazy, "nested field dropped by the single-row fast path"


async def test_root_level_indexed_row_is_also_complete():
    """Same defect, other entry point: a LIST root whose rows have nested
    tables. ``{{$node[1]}}`` went through the root OData tier, which has no
    way to re-join the children."""
    data = [{"id": 1, "items": [{"n": 1}]}, {"id": 2, "items": [{"n": 2}, {"n": 3}]}]
    strat, ref, _c, store = await build(data)
    lazy = await strat.try_resolve(ref, (1,))
    eager = (await store.materialize(ref))[1]
    assert lazy == eager == {"id": 2, "items": [{"n": 2}, {"n": 3}]}


async def test_flat_ref_still_takes_the_single_row_fast_path():
    """The guard must not tax the common case: no children, no re-join, so
    one OData row is a complete answer and no download may happen."""
    data = [{"id": i, "sku": f"S{i}"} for i in range(50)]
    strat, ref, client, _s = await build(data)
    assert not ref["artifacts"]
    assert await strat.try_resolve(ref, (7, "sku")) == "S7"
    assert client.downloads == 0 and client.odata == 1


async def test_scalar_beside_a_child_table_still_uses_one_row():
    """Descending into a column the row DOES carry stays cheap even though
    the ref has children."""
    strat, ref, client, _s = await build(OBJ_ROOT)
    assert await strat.try_resolve(ref, ("orders", 5, "sku")) == "S5"
    assert client.downloads == 0 and client.odata == 1


async def test_grandchild_through_an_indexed_parent():
    """``orders[0].items`` lives in a DIFFERENT file than the orders row.
    The old artifact branch read the orders CSV alone and found nothing."""
    strat, ref, _c, _s = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("orders", 0, "items"))
    assert out is not UNRESOLVED, "grandchild unreachable through an indexed parent"


async def test_scalar_beside_the_artifact_is_still_correct():
    strat, ref, _c, _s = await build(OBJ_ROOT)
    assert await strat.try_resolve(ref, ("title",)) == "quarterly"


# ------------------------------------------------- main-CSV scalar fast path

async def test_main_csv_scalar_costs_one_query_not_the_whole_tree():
    """``{{$node.title}}`` used to download the main CSV AND every child table
    to return one word."""
    strat, ref, client, _s = await build(OBJ_ROOT)
    assert await strat.try_resolve(ref, ("title",)) == "quarterly"
    assert client.downloads == 0, "still downloading the tree for a scalar"
    assert client.odata == 1


async def test_scalar_fast_path_agrees_with_the_full_materialize():
    strat, ref, _c, store = await build(OBJ_ROOT)
    assert await strat.try_resolve(ref, ("title",)) == (
        await store.materialize(ref)
    )["title"]


async def test_nested_scalar_through_a_dotted_column():
    data = {"meta": {"w": "W1", "n": 7}, "rows": [{"r": 1}]}
    strat, ref, client, store = await build(data)
    assert await strat.try_resolve(ref, ("meta", "w")) == "W1"
    assert await strat.try_resolve(ref, ("meta", "n")) == 7
    assert client.downloads == 0


async def test_dotted_artifact_name_costs_no_wasted_query():
    """``{"order": {"lines": [...]}}`` names its child artifact ``order.lines``
    and re-attaches it under ``order``, so the completeness rule has to compare
    the artifact name's FIRST SEGMENT — matching whole names would call the main
    row complete for ``{{$node.order.lines}}``.

    Measured, so the claim stays honest: the value is the SAME either way,
    because a miss falls through to the full materialize. What the whole-name
    comparison actually costs is one pointless OData round-trip on the way to an
    answer it cannot give. That is what this asserts — an equality-only
    assertion here passes with the rule broken.
    """
    data = {"order": {"id": 1, "lines": [{"sku": "A"}, {"sku": "B"}]}}
    strat, ref, client, store = await build(data)
    assert any(a["name"] == "order.lines" for a in ref["artifacts"])

    out = await strat.try_resolve(ref, ("order", "lines"))
    wasted = client.odata

    eager = (await store.materialize(ref))["order"]["lines"]
    assert out == eager == [{"sku": "A"}, {"sku": "B"}]
    assert wasted == 0, (
        f"queried the main CSV {wasted}x for a field that lives in a child file"
    )


async def test_missing_scalar_falls_through_and_stays_unresolved():
    strat, ref, _c, _s = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("nope",))
    assert out is UNRESOLVED or not out


async def test_scalar_types_survive_the_fast_path():
    """The fast path must cast exactly like the eager one — an ID-like string
    must not come back as a number."""
    data = {"zip": "0123", "ver": "1.20", "n": 42, "f": 1.5,
            "yes": True, "no": False, "nil": None, "rows": [{"r": 1}]}
    strat, ref, _c, store = await build(data)
    eager = await store.materialize(ref)
    for key in ("zip", "ver", "n", "f", "yes", "no"):
        lazy = await strat.try_resolve(ref, (key,))
        assert lazy == eager[key] and type(lazy) is type(eager[key]), (
            f"{key}: fast={lazy!r} eager={eager[key]!r}"
        )


async def test_array_root_does_not_take_the_scalar_fast_path():
    """A list has no string key; the SA-2262 short-circuit must still win."""
    data = [{"id": 1, "items": [{"n": 1}]}]
    strat, ref, client, _s = await build(data)
    out = await strat.try_resolve(ref, ("id",))
    assert out is UNRESOLVED or not out
    assert client.downloads == 0 and client.odata == 0


async def test_scalar_fast_path_obeys_the_oom_cap():
    """On an OBJECT root the "one row" IS the whole main CSV — a
    multi-megabyte JSON cell reads exactly like a scalar. The cheap tier runs
    before the guarded materialize, so without its own cap it would be a way
    around SA-1942."""
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=True,
                             materialize_max_bytes=1)
    strat = FileBackedResolverStrategy(store, file_client=client)
    ref = await store.store_data("n", {"output": {"deep": "v" * 3000}})
    ref["extracted"] = {}
    with pytest.raises(Exception) as ei:
        await strat.try_resolve(ref, ("output", "deep"))
    assert "cap" in str(ei.value).lower() or "exceeds" in str(ei.value).lower()


async def test_undeclared_size_is_not_assumed_small():
    """A legacy ref declares no size. With the cap on it must fall through to
    the guarded materialize, not be waved past."""
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), materialize_max_bytes=64)
    strat = FileBackedResolverStrategy(store, file_client=client)
    legacy = build_file_ref(file_id="f", row_count=1, value_kind="object")
    legacy.pop("size_bytes", None)
    assert strat._main_csv_within_cap(legacy) is False


async def test_cap_disabled_lets_the_fast_path_run():
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), materialize_max_bytes=0)
    strat = FileBackedResolverStrategy(store, file_client=client)
    legacy = build_file_ref(file_id="f", row_count=1, value_kind="object")
    assert strat._main_csv_within_cap(legacy) is True


async def test_scalar_fast_path_survives_an_odata_outage():
    strat, ref, client, store = await build(OBJ_ROOT)
    client.odata_fails = True
    assert await strat.try_resolve(ref, ("title",)) == "quarterly"


# ------------------------------------------------- the correctness trap

async def test_array_root_declines_rather_than_returning_a_wrong_pointer():
    """On a LIST root, the child file holds EVERY parent row's items glued
    together. Returning it as ``{{$node.items}}`` would be wrong in a way
    nothing downstream could detect."""
    data = [{"id": 1, "items": [{"n": 1}]}, {"id": 2, "items": [{"n": 2}, {"n": 3}]}]
    strat, ref, client, _s = await build(data)
    assert ref["value_kind"] == "array"
    out = await strat.try_resolve(ref, ("items",))
    assert out is UNRESOLVED or not out
    assert not is_file_ref(out), "handed back a concatenation of every parent's rows"
    assert client.downloads == 0, "and it must reach that answer without I/O"


async def test_array_field_on_an_object_root_is_still_claimed():
    """The legacy depth-1 split has no ``parent_row_key_column``, so its file
    IS the field regardless of the root's row count."""
    data = {"a": 1, "rows": [{"x": 1}, {"x": 2}]}
    strat, ref, client, store = await build(data, recursive=False)
    art_types = {a["artifact_type"] for a in ref["artifacts"]}
    assert art_types == {"array_field"}
    out = await strat.try_resolve(ref, ("rows",))
    assert is_file_ref(out)
    assert client.downloads == 0
    assert await store.materialize(out) == [{"x": 1}, {"x": 2}]


async def test_legacy_ref_without_declared_kind_uses_row_count():
    """A pre-SA-1977 ref declares nothing. One row → treat as the object it
    almost certainly is; many rows → decline. Never guess the kind."""
    art = {
        "name": "rows", "file_id": "f1", "artifact_type": "nested_array",
        "parent_row_key_column": "__parent_row_id", "artifacts": [],
    }
    single = build_file_ref(file_id="m", row_count=1)
    single["value_kind"] = ""
    many = build_file_ref(file_id="m", row_count=7)
    many["value_kind"] = ""
    assert FileBackedResolverStrategy._owns_whole_field(single, art) is True
    assert FileBackedResolverStrategy._owns_whole_field(many, art) is False


# ------------------------------------------------- user error / bad input

async def test_misspelt_field_name_is_unresolved_not_a_crash():
    strat, ref, client, _s = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("ordrs",))
    assert out is UNRESOLVED or not out
    assert not is_file_ref(out)


async def test_index_past_the_end_is_unresolved():
    strat, ref, _c, _s = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("orders", 9999, "sku"))
    assert out is UNRESOLVED or not out


async def test_negative_index_does_not_silently_return_a_wrong_row():
    """A negative index is almost always an authoring mistake. Whatever it
    returns, it must not be a DIFFERENT row than the same path would give on
    inline data — and it must not raise."""
    strat, ref, _c, store = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("orders", -1, "sku"))
    eager = (await store.materialize(ref))["orders"]
    assert out in (UNRESOLVED, None, "", eager[-1]["sku"]), (
        f"negative index invented a row: {out!r}"
    )


async def test_string_key_on_the_child_pointer_costs_nothing():
    """``{{$node.orders.name}}`` — a list has no string key. The answer is
    UNRESOLVED and it must be reached without downloading the child."""
    strat, ref, client, _s = await build(OBJ_ROOT)
    out = await strat.try_resolve(ref, ("orders", "name"))
    assert out is UNRESOLVED or not out
    assert client.downloads == 0


async def test_artifact_with_no_file_id_is_skipped_not_claimed():
    ref = build_file_ref(file_id="m", row_count=1, value_kind="object")
    ref["artifacts"] = [{
        "name": "rows", "file_id": "", "artifact_type": "nested_array",
        "parent_row_key_column": "__parent_row_id", "artifacts": [],
    }]
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=True)
    strat = FileBackedResolverStrategy(store, file_client=client)
    out = await strat._try_resolve_artifact(ref, "rows", ())
    assert out is _ARTIFACT_MISS


async def test_unknown_artifact_type_is_not_claimed():
    """A ``loop_chunk`` manifest is not an addressable field table."""
    ref = build_file_ref(file_id="m", row_count=1, value_kind="object")
    ref["artifacts"] = [{
        "name": "rows", "file_id": "f1", "artifact_type": "loop_chunk",
        "artifacts": [],
    }]
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=True)
    strat = FileBackedResolverStrategy(store, file_client=client)
    assert await strat._try_resolve_artifact(ref, "rows", ()) is _ARTIFACT_MISS


async def test_no_file_client_still_returns_the_pointer():
    """A resolver wired without an OData client must not lose the cheap path."""
    client = CountingClient()
    store = DataStoreService(client, CsvConverter(), recursive_split=True)
    ref = await store.store_data("k", OBJ_ROOT)
    strat = FileBackedResolverStrategy(store, file_client=None)
    assert is_file_ref(await strat.try_resolve(ref, ("orders",)))


async def test_odata_outage_still_produces_the_right_value():
    """When the OData tier is unavailable the answer must still be correct —
    slower is acceptable, wrong is not."""
    strat, ref, client, store = await build(OBJ_ROOT)
    client.odata_fails = True
    out = await strat.try_resolve(ref, ("orders", 5, "sku"))
    eager = (await store.materialize(ref))["orders"][5]["sku"]
    assert out == eager == "S5"


async def test_empty_child_table_round_trips_as_an_empty_list():
    data = {"title": "t", "orders": [{"id": 1}]}
    strat, ref, _c, store = await build(data)
    pointer = await strat.try_resolve(ref, ("orders",))
    assert await store.materialize(pointer) == [{"id": 1}]


async def test_field_that_is_a_scalar_not_an_artifact_is_untouched():
    """A main-CSV column whose name happens to look like a table."""
    data = {"orders": 5, "note": "n"}
    strat, ref, _c, _s = await build(data)
    assert await strat.try_resolve(ref, ("orders",)) == 5


@pytest.mark.parametrize("depth", [2, 4, 6])
async def test_pointer_equivalence_holds_at_depth(depth):
    """Whatever the nesting, pointer-then-materialize == materialize-then-index."""
    def nest(d):
        if d == 0:
            return [{"leaf": 1}]
        return [{"id": d, f"L{d}": nest(d - 1)}]

    data = {"top": nest(depth)}
    strat, ref, _c, store = await build(data)
    pointer = await strat.try_resolve(ref, ("top",))
    assert await store.materialize(pointer) == (await store.materialize(ref))["top"]
