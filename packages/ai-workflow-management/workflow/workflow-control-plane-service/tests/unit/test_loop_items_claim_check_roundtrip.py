"""The claim-check keeps its promise through the REAL store, not just in theory.

Spec: ``solace/specs/wcp-node-output-storage-redesign.md`` §15.3 fix #1.

``_claim_check_items`` (#1835) turns an oversized inline collection into a
pointer. That moves a loop from "index a Python list" to "fetch row *i* over
OData" — a different code path for every item the body ever sees. The unit tests
for it use a fake store and prove the DECISION; these prove the ROUND TRIP, on
the real ``DataStoreService`` + ``CsvConverter`` over the in-memory file service,
because the store is the half that can silently change the values.

Two shapes matter and neither had coverage through this path:

* **a list of objects** — the ordinary case, now file-backed for the first time
  when it happens to be large;
* **a list of scalars** — what ``loop_type: count`` produces
  (``list(range(max_iterations))``, default 10,000 ⇒ ~48 KB, comfortably past
  the 4 KB ceiling). Stored via the reserved ``__value`` column, so if the
  unwrap did not survive, every count loop would hand its body ``{"__value": 0}``
  instead of ``0``. That was recorded in the spec as an open item; it turns out
  the claim-check already covers it, which is worth pinning rather than
  re-deriving.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.orchestration.loop_driver.event_driven_loop_driver import (
    _LOOP_ITEMS_MAX_INLINE_BYTES,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.clients.mock_file_service_client import (
    MockFileServiceClient,
)


def _store() -> DataStoreService:
    return DataStoreService(MockFileServiceClient(), CsvConverter())


def _resolver() -> VariableResolver:
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _workflow(config: dict, items_value=None):
    loop_node = WorkflowNode(id="loop", name="loop", node_type=NodeType.LOOP,
                             config=config)
    body = WorkflowNode(id="body", name="body", node_type=NodeType.TRIGGER)
    exit_node = WorkflowNode(id="exit", name="exit", node_type=NodeType.TRIGGER)
    workflow = Workflow(id="wf", name="wf", nodes=[loop_node, body, exit_node],
                        edges=[
        WorkflowEdge(id="e-body", source_node_id="loop",
                     target_node_id="body", condition="body"),
        WorkflowEdge(id="e-exit", source_node_id="loop",
                     target_node_id="exit", condition="exit"),
    ])
    dag = GraphTopology(
        edge_map={"loop": workflow.edges, "body": [], "exit": []},
        adjacency={"loop": ["body", "exit"], "body": [], "exit": []},
        predecessors={"loop": [], "body": ["loop"], "exit": ["loop"]},
        entry_nodes=["loop"], leaf_nodes=["body", "exit"],
    )
    run = WorkflowRun(id="run-1", workflow_id="wf", context=RunContext(
        variables={"input": {"items": items_value}}, topology=dag))
    return loop_node, workflow, dag, run


async def _init(store, config, items_value=None):
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)
    loop_node, workflow, dag, run = _workflow(config, items_value)
    result = await driver.initialize_loop(
        loop_node, run, workflow, dag, None, TaskFactory())
    return driver, result.loop_state_updates["loop"], result


def _fat_objects(n: int = 400) -> list[dict]:
    """Comfortably past the 4 KB ceiling — ~30 B per row."""
    return [{"id": i, "sku": f"SKU-{i:04d}"} for i in range(n)]


class TestAFatCollectionOfObjects:
    @pytest.mark.asyncio
    async def test_it_becomes_a_pointer(self):
        driver, ls, _ = await _init(
            _store(), {"collection": "{{$input.items}}", "mode": "sequential"},
            _fat_objects())
        assert ls["items"].get("__file_ref") is True

    @pytest.mark.asyncio
    async def test_total_survives_the_move(self):
        """``total`` comes from ``row_count`` metadata on a pointer and from
        ``len`` on a list. A mismatch here silently truncates the loop."""
        items = _fat_objects()
        driver, ls, _ = await _init(
            _store(), {"collection": "{{$input.items}}", "mode": "sequential"},
            items)
        assert ls["total"] == len(items)

    @pytest.mark.asyncio
    async def test_every_item_reads_back_identical(self):
        items = _fat_objects(50) * 8          # >4 KB, small enough to compare
        driver, ls, _ = await _init(
            _store(), {"collection": "{{$input.items}}", "mode": "sequential"},
            items)
        read = [await driver.item_at(ls["items"], i) for i in range(len(items))]
        assert read == items

    @pytest.mark.asyncio
    async def test_the_first_item_dispatched_to_the_body_is_right(self):
        """``initialize_loop`` reads item 0 itself to seed the first body task —
        the one item that goes through a different call than the rest."""
        items = _fat_objects()
        driver, ls, result = await _init(
            _store(), {"collection": "{{$input.items}}", "mode": "sequential"},
            items)
        assert result.state_updates["loop"]["item"] == items[0]

    @pytest.mark.asyncio
    async def test_a_small_collection_is_untouched(self):
        """The other half of the contract: p50 total is 4, and those must not
        start paying an OData fetch per iteration."""
        items = [{"id": i} for i in range(4)]
        driver, ls, _ = await _init(
            _store(), {"collection": "{{$input.items}}", "mode": "sequential"},
            items)
        assert ls["items"] == items


class TestACountLoopStoresNoItemsAtAll:
    """``count`` and ``condition`` loops used to materialise
    ``list(range(max_iterations))`` — up to **10,000 integers, ~48 KB** — into
    ``loop_state``, for a workload that never reads them: the index IS the value,
    and a condition loop touches ``items`` only for the count.

    The claim-check made that visible rather than causing it. At the default
    ``max_iterations`` a materialised range is past the 4 KB ceiling, so it would
    upload a 10,000-row CSV at loop start and then pay an OData fetch per
    iteration to read back a number equal to the index it just asked for. Neither
    the list nor the file is the right answer; the range is."""

    @pytest.mark.asyncio
    async def test_it_is_a_marker_not_a_list(self):
        driver, ls, _ = await _init(
            _store(), {"loop_type": "count", "max_iterations": 10000,
                       "mode": "sequential"})
        assert ls["items"] == {"__index_range__": 10000}

    @pytest.mark.asyncio
    async def test_it_fits_the_state_json_budget(self):
        """The whole point: 10,000 integers do not fit ``STATE_JSON``'s 5000
        bytes, so before this the row was skipped and the loop's state was lost
        at cutover."""
        import json
        from app.layer2_application.data.loop_state_rows import STATE_JSON_MAX

        driver, ls, _ = await _init(
            _store(), {"loop_type": "count", "max_iterations": 10000,
                       "mode": "sequential"})
        assert len(json.dumps(ls["items"]).encode()) < 40
        assert len(json.dumps(ls["items"]).encode()) < STATE_JSON_MAX

    @pytest.mark.asyncio
    async def test_total_is_still_right(self):
        driver, ls, _ = await _init(
            _store(), {"loop_type": "count", "max_iterations": 2000,
                       "mode": "sequential"})
        assert ls["total"] == 2000

    @pytest.mark.asyncio
    async def test_the_body_gets_plain_integers(self):
        """The behaviour that must not change. ``$loop.item`` is the index."""
        driver, ls, result = await _init(
            _store(), {"loop_type": "count", "max_iterations": 2000,
                       "mode": "sequential"})
        assert result.state_updates["loop"]["item"] == 0
        assert [await driver.item_at(ls["items"], i) for i in (0, 1, 1999)] == [
            0, 1, 1999]

    @pytest.mark.asyncio
    async def test_out_of_range_is_none_not_an_index(self):
        """A list returns ``None`` past its end; the marker has to agree, or a
        loop that overruns gets a plausible number instead of a stop signal."""
        driver, ls, _ = await _init(
            _store(), {"loop_type": "count", "max_iterations": 3,
                       "mode": "sequential"})
        assert await driver.item_at(ls["items"], 3) is None
        assert await driver.item_at(ls["items"], -1) is None

    @pytest.mark.asyncio
    async def test_a_condition_loop_uses_it_too(self):
        """A condition loop's ``max_iterations`` is a ceiling it usually never
        reaches — materialising 10,000 placeholders for it was the worst case
        of the three."""
        driver, ls, _ = await _init(
            _store(), {"loop_type": "condition", "condition": "true",
                       "max_iterations": 10000, "mode": "sequential"})
        assert ls["items"] == {"__index_range__": 10000}

    @pytest.mark.asyncio
    async def test_it_is_never_uploaded(self):
        """A marker is not a list, so the claim-check leaves it alone — no CSV,
        no file id, nothing for retention to chase."""
        store = _store()
        driver, ls, _ = await _init(
            store, {"loop_type": "count", "max_iterations": 10000,
                    "mode": "sequential"})
        assert ls["items"].get("file_id") is None
        assert ls["items"].get("__file_ref") is None


class TestTheCeilingIsWhereItSaysItIs:
    @pytest.mark.asyncio
    async def test_just_under_stays_inline_and_just_over_does_not(self):
        """Pins the boundary against the real serializer rather than against a
        hand-computed size."""
        import json

        small = [{"v": "x" * 10} for _ in range(10)]
        assert len(json.dumps(small).encode()) < _LOOP_ITEMS_MAX_INLINE_BYTES
        _, ls_small, _ = await _init(
            _store(), {"collection": "{{$input.items}}"}, small)
        assert ls_small["items"] == small

        big = [{"v": "x" * 100} for _ in range(200)]
        assert len(json.dumps(big).encode()) > _LOOP_ITEMS_MAX_INLINE_BYTES
        _, ls_big, _ = await _init(
            _store(), {"collection": "{{$input.items}}"}, big)
        assert ls_big["items"].get("__file_ref") is True
