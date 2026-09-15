"""SA-1977 — a LOOP reads its collection from the DECLARED kind.

The bugs this closes, all reproduced on tenant-1 and all caused by the same
discarded fact — the root type of the node output the loop iterates:

* **``KeyError: 0``** (run FAILED, ``run.error = "0"``, because ``str(KeyError(0))``
  IS ``"0"``). A collection lazily kept as a file_ref pointer reached the engine,
  which indexed it as a list. ``len(pointer)`` is the pointer's KEY COUNT, so the
  bounds guard passed, and ``pointer[0]`` raised.
* **The green run that did nothing.** The SAME 1-element collection, when its item
  happened to contain a nested array, took the eager path instead: it materialized
  to a dict (row count 1 → "must be an object"), failed an ``isinstance(_, list)``
  check, logged a warning, and ran **zero** iterations — reporting COMPLETED.

One value, one item, two different wrong answers, decided by whether the item
happened to contain a nested array. Neither is fixed by fixing the other; only
declaring the kind fixes both.

These tests run against the REAL ``DataStoreService`` + ``CsvConverter`` over the
in-memory file service — the store IS the thing under test, so faking it would
test nothing.
"""

from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, RunContext, WorkflowRun
from app.layer1_domain.exceptions import LoopCollectionTypeError
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient


def _store(**kw) -> DataStoreService:
    return DataStoreService(MockFileServiceClient(), CsvConverter(), **kw)


def _resolver() -> VariableResolver:
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _loop_workflow(items_value):
    loop_node = WorkflowNode(
        id="loop", name="loop", node_type=NodeType.LOOP,
        config={"collection": "{{$input.items}}", "mode": "sequential"},
    )
    body = WorkflowNode(id="body", name="body", node_type=NodeType.TRIGGER)
    exit_node = WorkflowNode(id="exit", name="exit", node_type=NodeType.TRIGGER)
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[loop_node, body, exit_node],
        edges=[
            WorkflowEdge(id="e-body", source_node_id="loop", target_node_id="body", condition="body"),
            WorkflowEdge(id="e-exit", source_node_id="loop", target_node_id="exit", condition="exit"),
        ],
    )
    dag = GraphTopology(
        edge_map={"loop": workflow.edges, "body": [], "exit": []},
        adjacency={"loop": ["body", "exit"], "body": [], "exit": []},
        predecessors={"loop": [], "body": ["loop"], "exit": ["loop"]},
        entry_nodes=["loop"],
        leaf_nodes=["body", "exit"],
    )
    run = WorkflowRun(
        id="run-1", workflow_id="wf",
        context=RunContext(variables={"input": {"items": items_value}}, topology=dag),
    )
    return loop_node, workflow, dag, run


async def _init(store, collection):
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)
    loop_node, workflow, dag, run = _loop_workflow(collection)
    result = await driver.initialize_loop(
        loop_node, run, workflow, dag, None, TaskFactory(),
    )
    return driver, result


# ---------------------------------------------------------------------------
# The 1-element collection — both halves of the bug
# ---------------------------------------------------------------------------


async def test_one_element_lazy_collection_runs_exactly_one_iteration():
    """WAS: the pointer reached the engine → ``KeyError: 0`` → run FAILED("0")."""
    store = _store()
    ref = await store.store_data("src", [{"sku": "A"}])   # ONE element

    driver, result = await _init(store, ref)

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 1, "a 1-element collection has ONE iteration"
    # Still lazy: the pointer is what is persisted, not the rows.
    assert ls["items"].get("__file_ref") is True
    assert result.state_updates["loop"]["item"] == {"sku": "A"}
    # And the item is reachable by index without ever indexing the pointer.
    assert await driver.item_at(ls["items"], 0) == {"sku": "A"}


async def test_one_element_eager_collection_runs_one_iteration_not_zero():
    """WAS: materialized to a dict (1 row → "object") → items=[] → **0 iterations,
    run COMPLETED**. A green run that did nothing is the worst outcome of the two."""
    store = _store(recursive_split=True)
    # A nested array forces the eager path (artifacts → not lazy-range eligible).
    ref = await store.store_data("src", [{"sku": "A", "lines": [{"n": 1}]}])
    assert ref["artifacts"], "precondition: this ref takes the EAGER path"

    _driver, result = await _init(store, ref)

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 1, "eager path must agree with the lazy one: ONE iteration"
    assert ls["items"] == [{"sku": "A", "lines": [{"n": 1}]}]


async def test_lazy_and_eager_agree_on_the_same_value():
    """The two paths must not disagree about the same collection — the disagreement
    IS the bug family."""
    value = [{"sku": "A"}]
    lazy_store, eager_store = _store(), _store(recursive_split=True)
    lazy = await lazy_store.store_data("src", value)
    eager = await eager_store.store_data("src", value)

    _d1, r1 = await _init(lazy_store, lazy)
    _d2, r2 = await _init(eager_store, eager)

    assert r1.loop_state_updates["loop"]["total"] == r2.loop_state_updates["loop"]["total"] == 1
    assert r1.state_updates["loop"]["item"] == r2.state_updates["loop"]["item"] == value[0]


# ---------------------------------------------------------------------------
# A4 — a list of scalars is a real collection
# ---------------------------------------------------------------------------


async def test_loop_over_file_backed_scalars_yields_scalar_items():
    """``$loop.item`` must be ``1`` — the number — exactly as on the inline path,
    not the ``{"__value": 1}`` wrapper the storage format happens to use."""
    store = _store()
    ref = await store.store_data("src", [1, 2, 3])
    assert ref["value_kind"] == "array" and ref["item_kind"] == "number"

    driver, result = await _init(store, ref)

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 3
    assert ls["items"].get("__file_ref") is True, "still lazy — no whole-list download"
    assert result.state_updates["loop"]["item"] == 1
    # The LAZY row-range path (OData $skip/$top) must unwrap too, or the first
    # iteration gets a scalar and the rest get wrapper dicts.
    assert [await driver.item_at(ls["items"], i) for i in range(3)] == [1, 2, 3]


async def test_loop_over_file_backed_strings_yields_scalar_items():
    store = _store()
    ref = await store.store_data("src", ["a", "b"])
    driver, result = await _init(store, ref)
    ls = result.loop_state_updates["loop"]
    assert [await driver.item_at(ls["items"], i) for i in range(2)] == ["a", "b"]


# ---------------------------------------------------------------------------
# A2 — a declared non-array fails FAST
# ---------------------------------------------------------------------------


async def test_loop_over_declared_object_fails_fast():
    """WAS: a warning log and ``items = []`` → 0 iterations, run COMPLETED.
    Silently skipping the work is worse than failing."""
    store = _store()
    ref = await store.store_data("src", {"sku": "A"})   # an OBJECT, not a list
    assert ref["value_kind"] == "object"

    with pytest.raises(LoopCollectionTypeError) as exc:
        await _init(store, ref)

    # The message must name the actual mistake — not surface as "0".
    assert "not an array" in str(exc.value)
    assert exc.value.kind == "object"


@pytest.mark.parametrize(
    "collection", [{"sku": "A"}, "hello", 42], ids=["object", "string", "number"],
)
async def test_loop_over_an_INLINE_non_collection_also_fails_fast(collection):
    """The fail-fast must not depend on whether the upstream node happened to be
    file-backed. A ``code`` node keeps its output INLINE
    (``should_keep_inline``), so ``LOOP over {{$codeNode.out}}`` where ``out`` is
    an object took the inline path and ran zero iterations, green — while the
    identical mistake against an http-worker's (file-backed) object failed loudly.
    Same error, two behaviours, decided by the storage of the upstream node: that
    is the bug family, not the fix."""
    with pytest.raises(LoopCollectionTypeError):
        await _init(_store(), collection)


async def test_loop_over_an_absent_collection_is_still_an_empty_run():
    """``None`` is the deliberate exception: an absent/unresolved value means
    "nothing to iterate", which is a legitimate empty run — and it is what a
    file-backed ``null`` does too, so the two agree."""
    _driver, result = await _init(_store(), None)
    assert result.loop_state_updates["loop"]["total"] == 0


async def test_strict_collection_can_be_switched_off_without_a_deploy():
    """The kill switch. The legacy-ref fallback protects NOTHING here — a live run
    re-executes its upstream node, which writes a FRESH ref declaring
    ``object`` — so every workflow that today loops zero times over an object goes
    red on its next run. That is the ticket's acceptance, and it is still a real
    behaviour change on a deployed product, so it must be reversible with one env
    var rather than a code deploy. OFF restores the old count — but LOUDLY."""
    import os

    store = _store()
    ref = await store.store_data("src", {"sku": "A"})
    old = os.environ.get("LOOP_STRICT_COLLECTION")
    os.environ["LOOP_STRICT_COLLECTION"] = "false"
    try:
        _driver, result = await _init(store, ref)
        assert result.loop_state_updates["loop"]["total"] == 0, "old behaviour restored"
    finally:
        if old is None:
            os.environ.pop("LOOP_STRICT_COLLECTION", None)
        else:
            os.environ["LOOP_STRICT_COLLECTION"] = old

    # …and ON by default, which is the acceptance.
    with pytest.raises(LoopCollectionTypeError):
        await _init(store, ref)


async def test_the_loop_node_itself_goes_red_not_just_the_run():
    """A typed error that fails the RUN but leaves the LOOP node ``READY`` and
    non-terminal points at nothing. ``LoopCollectionTypeError`` must be handled on
    the same seam as an unresolvable expression — ``NodeDispatched`` +
    ``NodeFailed`` — so the canvas marks the node the author must fix."""
    from app.layer2_application.orchestration import orchestration_engine as oe
    from app.layer2_application.orchestration import loop_iteration_driver as lid
    import inspect

    for mod in (oe, lid):
        src = inspect.getsource(mod)
        assert "InputRequirementError, LoopCollectionTypeError" in src, (
            f"{mod.__name__} must fail the NODE, not escape to the run catch-all"
        )


async def test_loop_type_error_reaches_the_run_not_a_log_line():
    """The typed error is only worth anything if it PROPAGATES. Assert it through
    the real handler seam (``LoopNodeHandler.handle``), which is what the engine
    invokes — the engine's catch-all then fails the run with ``str(exc)``, i.e.
    the readable message, instead of the string "0"."""
    from app.layer2_application.node_handlers.loop_handler import LoopNodeHandler

    store = _store()
    ref = await store.store_data("src", {"sku": "A"})
    handler = LoopNodeHandler(_resolver(), data_store_service=store)
    loop_node, workflow, dag, run = _loop_workflow(ref)

    with pytest.raises(LoopCollectionTypeError) as exc:
        await handler.handle(loop_node, run, workflow, dag, None, TaskFactory())

    # This string is what the user sees as run.error. It used to be "0".
    assert "not an array" in str(exc.value)


async def test_loop_over_empty_declared_array_runs_zero_iterations():
    """Zero iterations is CORRECT here — and it is now distinguishable from the
    silent-zero bug, because ``[]`` declares itself an array. ``[]`` and ``{}``
    used to store the same zero bytes and read back identically."""
    store = _store()
    ref = await store.store_data("src", [])
    assert ref["value_kind"] == "array"

    _driver, result = await _init(store, ref)
    assert result.loop_state_updates["loop"]["total"] == 0


# ---------------------------------------------------------------------------
# The engine's side of the same line
# ---------------------------------------------------------------------------


async def test_item_at_never_indexes_a_pointer():
    """The engine used to do ``items[i]`` where ``items`` may be a file_ref. This
    asserts the shape of the old crash, and that the accessor does not have it."""
    store = _store()
    ref = await store.store_data("src", [{"sku": "A"}])
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)

    with pytest.raises(KeyError):
        _ = ref[0]                       # ← the old engine line, verbatim
    assert len(ref) > 1                  # ← and why the bounds guard did not save it

    assert await driver.item_at(ref, 0) == {"sku": "A"}
    assert await driver.item_at(ref, 99) is None, "out of range → None, not a crash"


async def test_resolver_carries_the_declaration_into_the_pointer_it_mints():
    """``{{$node.items}}`` does not hand the loop the stored ref — the resolver
    MINTS A FRESH ONE for the array_field artifact. Dropping the kind there would
    undo the whole contract at the exact site the original ``KeyError: 0``
    travelled through: the loop would get an undeclared pointer, and a 1-row
    artifact would materialize as a bare dict while ``materialize($node)["items"]``
    gave a list — two answers for the same bytes."""
    from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy

    store = _store()
    # A dict with an array-of-objects field → an ``array_field`` artifact.
    ref = await store.store_data("src", {"name": "x", "items": [{"sku": "A"}]})
    art = ref["artifacts"][0]
    assert art["value_kind"] == "array" and art["item_kind"] == "object", "precondition"

    strategy = FileBackedResolverStrategy(store, store._file_client)
    pointer = await strategy._try_resolve_artifact(ref, "items", ())

    assert pointer["value_kind"] == "array", "the minted pointer must declare its kind"
    assert pointer["item_kind"] == "object"
    # And it must survive as a 1-element list, not collapse to the row-count guess.
    assert await store.materialize(pointer) == [{"sku": "A"}]


async def test_engine_loop_item_at_reads_a_pointer():
    from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine

    store = _store()
    ref = await store.store_data("src", [{"sku": "A"}, {"sku": "B"}])
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)

    engine = OrchestrationEngine.__new__(OrchestrationEngine)
    engine._event_driven_loop_driver = driver

    assert await engine._loop_item_at(ref, 1) == {"sku": "B"}
    assert await engine._loop_item_at([{"sku": "X"}], 0) == {"sku": "X"}, "inline still works"

    # No driver wired (bare engine in a unit test): a pointer must degrade to
    # None, never to KeyError.
    engine._event_driven_loop_driver = None
    assert await engine._loop_item_at(ref, 0) is None
