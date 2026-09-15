"""SA-1982 (A5) — the round-trip conformance corpus for SA-1977.

The invariant, asserted for every shape, through every storage mode, against
**both** producers::

    materialize(store(v)) == v
    kind_of(materialize(store(v))) == kind_of(v)

Two things make this the load-bearing test of the Story rather than a nice-to-have:

1. **Two writers of one fact will drift.** The root kind is now written by the
   Python ``CsvConverter`` *and* by the Rust ``json-csv-streamer``. A corpus that
   ran against only one of them would not notice them disagreeing — and the
   whole point of a declared kind is that everybody reads the same declaration.
   A shape supported by only one producer is a defect here, not a gap.

2. **Every loop test in the suite iterates an INLINE list**, which is exactly why
   the lazy / file-backed branch had zero coverage and shipped broken
   (``KeyError: 0``). The same blindness is what this corpus is built to remove:
   the shapes are stored and read BACK THROUGH THE STORE, never asserted against
   the writer's own idea of what it wrote.

The heterogeneous-column shapes at the bottom are SA-1984's, not SA-1977's — a
field that is ``null`` in one record and an object in another. They are here, and
marked ``xfail(strict=True)``, because the corpus that would have caught them is
this one, and a strict xfail turns green into a build failure the moment SA-1984
lands — so the marker cannot outlive the bug.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import is_file_ref
from app.layer1_domain.value_objects.value_kind import (
    ARRAY,
    BOOLEAN,
    NULL,
    NUMBER,
    OBJECT,
    STRING,
    kind_of,
    item_kind_of,
)
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


# ---------------------------------------------------------------------------
# The corpus — ROOT kinds (SA-1977)
# ---------------------------------------------------------------------------

ROOT_CORPUS: list[tuple[str, object, str, str]] = [
    # (label,                    value,                        value_kind, item_kind)
    ("object",                   {"a": 1, "b": "x"},           OBJECT,  ""),
    ("empty object",             {},                           OBJECT,  ""),
    ("list[object] x2",          [{"a": 1}, {"a": 2}],         ARRAY,   OBJECT),
    # ↓ the two that were silently LOST before this Story
    ("list[object] x1",          [{"a": 1}],                   ARRAY,   OBJECT),
    ("empty list",               [],                           ARRAY,   ""),
    # ↓ the shapes whose WRITER used to raise AttributeError
    ("list[number]",             [1, 2, 3],                    ARRAY,   NUMBER),
    ("list[string]",             ["a", "b"],                   ARRAY,   STRING),
    ("list[string] with empty",  ["a", ""],                    ARRAY,   STRING),
    ("list[bool]",               [True, False],                ARRAY,   BOOLEAN),
    ("list[number] with null",   [1, None, 3],                 ARRAY,   NUMBER),
    ("list[null]",               [None, None],                 ARRAY,   NULL),
    ("list[array]",              [[1, 2], [3]],                ARRAY,   ARRAY),
    ("list mixed kinds",         [1, "a", True, None],         ARRAY,   ""),
    # ↓ every one of these was found by RED-TEAMING this change, not by writing
    #   it. Each was silently corrupted, and each was green in this same suite.
    # A string list containing a null: None and "" both write the empty cell, so
    # ["a", None, "b"] read back as ["a", "", "b"] — the null became a string.
    ("list[string] with null",   ["a", None, "b"],             ARRAY,   STRING),
    ("list[string] null+empty",  ["a", "", None],              ARRAY,   STRING),
    # A SPARSE ARRAY OF OBJECTS — the shape any external API returns. The writer
    # stored it in __value (an element that is not a dict) while declaring
    # array<object> (a rule that let nulls abstain), and the reader believed the
    # declaration and refused to unwrap: every element came back wearing a
    # phantom {"__value": …} key, and $loop.item.id resolved to nothing.
    ("sparse list[object]",      [{"id": 1}, None, {"id": 3}], ARRAY,   ""),
    # A node output whose fields are literally named like the reserved column.
    # The first unwrap sniffed the row's columns instead of reading the
    # declaration, and turned these dicts into bare scalars.
    ("list of __value dicts",    [{"__value": 1}, {"__value": 2}], ARRAY, OBJECT),
    ("one __value dict",         [{"__value": "a"}],           ARRAY,   OBJECT),
    ("__value beside a real key", [{"__value": 1, "x": 2}],    ARRAY,   OBJECT),
    ("scalar string",            "hello",                      STRING,  ""),
    ("scalar number",            42,                           NUMBER,  ""),
    ("scalar float",             1.5,                          NUMBER,  ""),
    ("scalar bool",              True,                         BOOLEAN, ""),
    ("scalar empty string",      "",                           STRING,  ""),
    ("null",                     None,                         NULL,    ""),
    # A JSON-array-STRING is a STRING. It must not be promoted to an array
    # just because it starts with "[" — the declared column type is what stops
    # ``_try_parse_value`` from guessing.
    ("list[json-array-string]",  ["[1,2,3]", "[4]"],           ARRAY,   STRING),
    ("nested arrays (artifacts)", [{"a": 1, "lines": [{"n": 1}]}], ARRAY, OBJECT),
    # ↓ found in red-team round 3. A list of EMPTY objects: the Python writer
    #   emitted blank lines, ``inject_row_ids`` dropped them, and the file held
    #   ZERO rows while the ref declared row_count=len(data). Harmless-looking
    #   until the declaration became load-bearing: a lazy LOOP then ran a phantom
    #   iteration with a null item, and the resolver raised a false "the file
    #   download likely failed". The Rust writer got it right; they disagreed.
    ("list of empty objects",    [{}, {}],                     ARRAY,   OBJECT),
    # ↓ a sparse array as a FIELD — the shape that actually occurs in production
    #   (an external API's record list). It CRASHED the writer:
    #   ``'NoneType' object has no attribute 'items'`` ⇒ the node output could not
    #   be stored at all. Fixing only the ROOT would have left the only reachable
    #   instance of it broken.
    ("sparse array as a FIELD",  {"records": [{"id": 1}, None, {"id": 3}], "n": 3},
                                                               OBJECT,  ""),
    ("big ints (past i64/u64)",  [10**25, -(10**19), 2**64 - 1], ARRAY, NUMBER),
]


def _store(**kw) -> DataStoreService:
    return DataStoreService(MockFileServiceClient(), CsvConverter(), **kw)


MODES = {
    # the default in-RAM Python writer
    "python": {},
    # SA-1910 recursive nested-array split
    "recursive": {"recursive_split": True},
    # SA-1905 native Rust streamer (threshold 1 byte → always streams)
    "streaming": {"chunked_output_enabled": True, "chunking_threshold_bytes": 1},
    # BOTH flags on — the configuration nobody tested, and the one where the
    # streaming gate silently swallowed the recursive split (nested arrays back
    # to JSON cells, child artifacts gone, no warning) until the ordering was
    # restored. Testing two features separately does not test them together.
    "recursive+streaming": {
        "recursive_split": True,
        "chunked_output_enabled": True,
        "chunking_threshold_bytes": 1,
    },
}


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize(
    "label,value,expected_kind,expected_item",
    ROOT_CORPUS,
    ids=[c[0] for c in ROOT_CORPUS],
)
async def test_root_kind_roundtrip(mode, label, value, expected_kind, expected_item):
    """store → materialize returns the SAME value AND the SAME root kind."""
    if mode == "streaming":
        pytest.importorskip("json_csv_streamer")

    store = _store(**MODES[mode])
    ref = await store.store_data("node-1", value)

    # Every stored value is a file_ref, and it DECLARES what it is.
    assert is_file_ref(ref), f"{label}: store_data must return a file_ref"
    assert kind_of(ref) == expected_kind, f"{label}: declared value_kind"
    if expected_item:
        assert item_kind_of(ref) == expected_item, f"{label}: declared item_kind"

    out = await store.materialize(ref)
    assert out == value, f"{label}: value must round-trip exactly"
    assert kind_of(out) == kind_of(value), f"{label}: kind must round-trip"


@pytest.mark.parametrize(
    "label,value,expected_kind,expected_item",
    ROOT_CORPUS,
    ids=[c[0] for c in ROOT_CORPUS],
)
async def test_producers_agree(label, value, expected_kind, expected_item):
    """The Python writer and the Rust streamer declare the SAME kind for the
    same value — the drift this corpus exists to prevent."""
    pytest.importorskip("json_csv_streamer")
    if not isinstance(value, list) or not value:
        pytest.skip("the streamer's input is a JSONL file — i.e. a non-empty list")

    py = await _store().store_data("n", value)
    rs = await _store(
        chunked_output_enabled=True, chunking_threshold_bytes=1,
    ).store_data("n", value)

    assert kind_of(py) == kind_of(rs) == expected_kind, label
    assert item_kind_of(py) == item_kind_of(rs), f"{label}: item_kind must agree"
    assert (
        await _store().materialize(py)
        == await _store(chunked_output_enabled=True).materialize(rs)
    ), f"{label}: both producers must materialize to the same value"


# ---------------------------------------------------------------------------
# The bug this contract does NOT close, kept where it will be noticed
# ---------------------------------------------------------------------------

# SA-1984 B1 — CLOSED. A leaf column and a branch column at the same path used to
# make the read throw (null row first) or silently destroy the object (object row
# first); ``CsvConverter._resolve_collisions`` now decides from the row's own
# cells instead of from column order. Retroactive: files already at rest carry
# both columns, so outputs written before the fix read back correctly with no
# migration. These stay parametrized separately from the still-open cell-type
# shapes below so a regression names itself.
LEAF_BRANCH_COLLISION_CORPUS: list[tuple[str, object]] = [
    ("null-or-object, null FIRST",    [{"x": None}, {"x": {"a": 1}}]),
    ("null-or-object, object FIRST",  [{"x": {"a": 1}}, {"x": None}]),
    ("array-or-object in one cell",   [{"p": [1, 2]}, {"p": {"a": 1}}]),
]

# Still open: B2 (column type must be the UNION across rows, not the first row's),
# B3 (``"json"`` must split into array/object so a JSON-array STRING and a real
# array stop writing byte-identical cells), and the CSV-rectangle limitation that
# turns an absent key into an explicit ``null``.
CELL_TYPE_CORPUS: list[tuple[str, object]] = [
    ("number-or-string",              [{"id": "007"}, {"id": 7}]),
    ("json-array-string vs array",    [{"p": "[1,2,3]"}, {"p": [4, 5, 6]}]),
    ("array vs json-array-string",    [{"p": [1, 2, 3]}, {"p": "[4,5,6]"}]),
    ("sparse / union columns",        [{"a": 1}, {"b": 2}]),
]

HETEROGENEOUS_CORPUS: list[tuple[str, object]] = (
    LEAF_BRANCH_COLLISION_CORPUS + CELL_TYPE_CORPUS
)


@pytest.mark.parametrize(
    "label,value", LEAF_BRANCH_COLLISION_CORPUS,
    ids=[c[0] for c in LEAF_BRANCH_COLLISION_CORPUS],
)
async def test_leaf_branch_collision_roundtrip(label, value):
    """SA-1984 B1 — both row orders must round-trip EXACTLY.

    One order used to raise ``TypeError`` and the other silently returned
    ``None`` where the object had been; asserting only one of them passes while
    the product corrupts data, so both are pinned here.
    """
    store = _store()
    ref = await store.store_data("node-1", value)
    assert await store.materialize(ref) == value


@pytest.mark.xfail(
    strict=True,
    reason="SA-1984 B2/B3/B4 — per-CELL types, one level below the root kind "
           "this Story declares: the column type is taken from the FIRST row, "
           "and both list and dict map to \"json\" so a JSON-array string and a "
           "real array write identical cells. Shipping SA-1977 changes none of "
           "it. Strict, so this marker cannot outlive the fix.",
)
@pytest.mark.parametrize(
    "label,value", CELL_TYPE_CORPUS, ids=[c[0] for c in CELL_TYPE_CORPUS],
)
async def test_heterogeneous_columns_roundtrip(label, value):
    store = _store()
    ref = await store.store_data("node-1", value)
    assert await store.materialize(ref) == value


@pytest.mark.parametrize(
    "label,value", HETEROGENEOUS_CORPUS, ids=[c[0] for c in HETEROGENEOUS_CORPUS],
)
async def test_heterogeneous_columns_keep_their_root_kind(label, value):
    """SA-1984 corrupts the CELLS, but SA-1977's declaration must still hold: a
    list is still declared a list, whatever happens inside its columns. If this
    ever fails, the two bugs have become entangled."""
    store = _store()
    ref = await store.store_data("node-1", value)
    assert kind_of(ref) == ARRAY
    assert item_kind_of(ref) == OBJECT


# ---------------------------------------------------------------------------
# The reserved column must not eat real data (found by red-teaming this change)
# ---------------------------------------------------------------------------

VALUE_COLUMN_TRAPS: list[tuple[str, object]] = [
    # The first version of the __value unwrap decided "is this a scalar wrapper?"
    # by looking at the row's COLUMNS — and so it turned an ordinary list of
    # one-key dicts into a list of scalars, destroying them. It did that while the
    # ref sat there declaring item_kind="object". Re-deriving from the bytes what
    # the writer already wrote down is the disease this Story cures; it does not
    # stop being the disease when the new code does it.
    ("list of one-key __value dicts", [{"__value": 1}, {"__value": 2}]),
    ("one one-key __value dict",      [{"__value": "a"}]),
    ("__value alongside a real key",  [{"__value": 1, "x": 2}]),
    ("object whose only key is __value", {"__value": 7}),
]


@pytest.mark.parametrize(
    "label,value", VALUE_COLUMN_TRAPS, ids=[c[0] for c in VALUE_COLUMN_TRAPS],
)
async def test_value_column_never_eats_a_real_field(label, value):
    store = _store()
    ref = await store.store_data("node-1", value)
    assert await store.materialize(ref) == value, "eager path must not unwrap it"


@pytest.mark.parametrize(
    "label,value", VALUE_COLUMN_TRAPS[:3], ids=[c[0] for c in VALUE_COLUMN_TRAPS[:3]],
)
async def test_value_column_never_eats_a_real_field_lazily(label, value):
    """The lazy row-range path had the identical bug — and it is the path a big
    LOOP takes, so it is the one that would have corrupted production data."""
    store = _store()
    ref = await store.store_data("node-1", value)
    assert await store.materialize_range(ref, 0, 10) == value


async def test_two_nodes_with_byte_identical_csv_do_not_share_a_decoded_item():
    """**Identical bytes are not an identical value.**

    Uploads are content-addressed, so ``[{"id":"1"}]`` and ``[{"id":1}]`` write
    byte-identical CSV and dedupe to ONE ``file_id`` — but they decode through
    different ``column_types``, which live on the **ref**, not in the file. A
    ``(file_id, index)`` memo on the engine therefore handed the second node's
    loop the FIRST node's item. Keying a *decoded* value by ``file_id`` alone is
    precisely the inference this Story deletes, so the memo is gone; this test
    stops anyone re-adding it."""
    from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
    from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
    from app.layer2_application.resolve.variable_resolver import (
        MetadataResolverStrategy, TraversalResolverStrategy, VariableResolver,
    )

    store = _store()
    a = await store.store_data("nodeA", [{"id": "1"}, {"id": "2"}])   # strings
    b = await store.store_data("nodeB", [{"id": 1}, {"id": 2}])       # numbers
    assert a["file_id"] == b["file_id"], "precondition: content-hash dedupe"
    assert a["column_types"] != b["column_types"]

    driver = EventDrivenLoopDriver(
        VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()]),
        data_store_service=store,
    )
    engine = OrchestrationEngine.__new__(OrchestrationEngine)
    engine._event_driven_loop_driver = driver

    assert await engine._loop_item_at(a, 0) == {"id": "1"}
    assert await engine._loop_item_at(b, 0) == {"id": 1}, "must not serve nodeA's item"


async def test_indexed_reference_strips_synthetic_columns_like_every_other_reader():
    """``{{$node[i]}}`` handed the worker ``{'__row_id': 1, 'sku': 'A1', 'qty': 3,
    '_row_index': 0}`` while ``materialize()[0]`` said ``{'sku': 'A1', 'qty': 3}``
    — same file, same row, two answers, and the leaky one is what a worker
    actually receives (the index fast-path wins). The commonest shape in the
    product."""
    from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy

    store = _store()
    ref = await store.store_data("http1", [{"sku": "A1", "qty": 3}, {"sku": "B2", "qty": 5}])
    strategy = FileBackedResolverStrategy(store, store._file_client)

    got = await strategy.try_resolve(ref, (0,))
    assert got == (await store.materialize(ref))[0] == {"sku": "A1", "qty": 3}
    assert "__row_id" not in got and "_row_index" not in got


async def test_indexed_reference_agrees_with_materialize_on_a_scalar_list():
    """``{{$node[i]}}`` takes an OData index fast-path that runs BEFORE the
    materialize fallback — so it is what every indexed reference actually gets.
    It was the fourth reader of a stored CSV and the only one that never learned
    about ``__value``: on a ``list[scalar]`` it handed the worker
    ``{'__row_id': 2, '__value': 20, '_row_index': 1}`` while ``materialize()[1]``
    and ``materialize_range(ref,1,1)`` — the SAME file, the SAME OData query —
    both said ``20``."""
    from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy

    store = _store()
    ref = await store.store_data("n", [10, 20, 30])
    strategy = FileBackedResolverStrategy(store, store._file_client)

    assert await strategy.try_resolve(ref, (1,)) == 20
    assert (await store.materialize(ref))[1] == 20
    assert await store.materialize_range(ref, 1, 1) == [20]


async def test_streaming_does_not_swallow_the_recursive_split():
    """A round-trip assertion cannot see this: with the split disabled the data
    still comes back intact (nested arrays just become JSON cells). What is LOST
    is the ``nested_array`` child artifact — and with it OData row access and the
    steward's nested table editor. So assert the ARTIFACT, not the value.

    The streaming gate was briefly checked ahead of the recursive split, which
    silently disabled SA-1910 for every list over the chunking threshold whenever
    both flags were on — the natural end state, since both are documented as safe
    to enable."""
    pytest.importorskip("json_csv_streamer")
    value = [{"id": 1, "lines": [{"n": 1}]}, {"id": 2, "lines": [{"n": 2}]}]

    recursive_only = await _store(recursive_split=True).store_data("n", value)
    both = await _store(**MODES["recursive+streaming"]).store_data("n", value)

    kinds = lambda ref: [a.get("artifact_type") for a in ref.get("artifacts", [])]
    assert kinds(recursive_only) == ["nested_array"], "precondition"
    assert kinds(both) == ["nested_array"], (
        "turning streaming on must not silently disable the recursive split"
    )
