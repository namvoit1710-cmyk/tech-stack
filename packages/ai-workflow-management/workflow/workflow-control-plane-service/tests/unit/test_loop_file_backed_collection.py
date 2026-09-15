"""SA-1905 D2 (SA-1912) — LOOP over a file-backed collection.

Regression + feature coverage for the silent 0-iteration bug: when a
loop's ``collection`` expression (``{{$node.items}}``) resolves to a
file-backed *array_field* reference, the resolver returns a bare
``file_ref`` dict (so **worker** inputs stay claim-check pointers, not
downloads). The loop driver is a *whole-value consumer* — it needs a
real sequence — so it must materialize the reference via the injected
``DataStoreService`` before iterating. Prior behaviour: ``isinstance(
resolved, list)`` was False → ``items = []`` → the loop silently ran
**zero** iterations.

These tests assert:
  1. A file-backed collection is materialized and iterated N times.
  2. An inline list still works and the store is NOT consulted (no
     needless download for the common case).
  3. With no store wired (legacy construction), the loop degrades
     gracefully to empty instead of crashing.
"""

from __future__ import annotations

from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType


def _file_ref(file_id: str = "f-items", row_count: int = 3) -> dict:
    """A file_ref as returned by ``FileBackedResolverStrategy._try_resolve_artifact``
    for a whole array_field collection (empty remaining path)."""
    return {
        "__file_ref": True,
        "artifact_id": "__root__",
        "file_id": file_id,
        "content_type": "text/csv",
        "row_count": row_count,
        "columns": [],
        "column_types": {},
        "artifacts": [],
        "version": 0,
        "etag": "",
    }


class _FakeStore:
    """Minimal DataStoreService stand-in: records materialize/range calls."""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.materialize_calls = 0
        self.range_calls = 0

    @staticmethod
    def is_lazy_range_eligible(value) -> bool:
        return bool(
            isinstance(value, dict)
            and value.get("__file_ref") is True
            and value.get("file_id")
            and not value.get("artifacts")
        )

    async def materialize(self, file_ref: dict):
        self.materialize_calls += 1
        return list(self._rows)

    async def materialize_range(self, file_ref: dict, skip: int, top: int):
        self.range_calls += 1
        return list(self._rows[skip: skip + top])


def _resolver() -> VariableResolver:
    return VariableResolver([MetadataResolverStrategy(), TraversalResolverStrategy()])


def _loop_workflow(items_value):
    """LOOP(collection={{$input.items}}) → body(TRIGGER) → exit(TRIGGER)."""
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


async def test_loop_over_flat_file_backed_collection_iterates_LAZILY():
    # SA-1905 D2.4: a flat streamed collection (artifacts: []) is iterated
    # LAZILY — the loop keeps the file_ref pointer in loop_state (never
    # downloads the whole array) and fetches rows one at a time by range.
    rows = [{"sku": "A"}, {"sku": "B"}, {"sku": "C"}]
    store = _FakeStore(rows)
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)
    ref = _file_ref(row_count=3)
    loop_node, workflow, dag, run = _loop_workflow(ref)

    result = await driver.initialize_loop(loop_node, run, workflow, dag, None, TaskFactory())

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 3, "row_count metadata drives the count, no download"
    # The PERSISTED loop_state holds the serializable file_ref pointer, NOT the
    # materialized rows — this is what keeps a huge loop out of CP RAM.
    assert ls["items"].get("__file_ref") is True
    assert ls["items"]["file_id"] == ref["file_id"]
    assert store.materialize_calls == 0, "must NOT full-download a lazy collection"
    assert store.range_calls >= 1, "rows are fetched by range on demand"
    # The first iteration's loop-var item is the real row 0 (range-fetched).
    assert result.state_updates["loop"]["item"] == rows[0]


async def test_loop_file_backed_with_artifacts_falls_back_to_materialize():
    # A non-flat file_ref (has artifacts → recursive/nested) is NOT lazy-range
    # eligible; the loop falls back to a bounded full materialize.
    rows = [{"sku": "A"}, {"sku": "B"}]
    store = _FakeStore(rows)
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)
    ref = _file_ref(row_count=2)
    ref["artifacts"] = [{"artifact_type": "nested_array", "file_id": "f-child"}]
    loop_node, workflow, dag, run = _loop_workflow(ref)

    result = await driver.initialize_loop(loop_node, run, workflow, dag, None, TaskFactory())

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 2
    assert ls["items"] == rows, "non-flat ref → materialized to a concrete list"
    assert store.materialize_calls == 1
    assert store.range_calls == 0


async def test_loop_over_inline_list_does_not_touch_store():
    rows = [{"sku": "A"}, {"sku": "B"}]
    store = _FakeStore([])  # would return [] and break the assert if wrongly consulted
    driver = EventDrivenLoopDriver(_resolver(), data_store_service=store)
    loop_node, workflow, dag, run = _loop_workflow(rows)

    result = await driver.initialize_loop(loop_node, run, workflow, dag, None, TaskFactory())

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 2
    assert ls["items"] == rows
    assert store.materialize_calls == 0, "inline lists must not trigger a download"


async def test_loop_file_backed_without_store_degrades_gracefully():
    driver = EventDrivenLoopDriver(_resolver())  # legacy construction, no store
    loop_node, workflow, dag, run = _loop_workflow(_file_ref(row_count=5))

    result = await driver.initialize_loop(loop_node, run, workflow, dag, None, TaskFactory())

    ls = result.loop_state_updates["loop"]
    assert ls["total"] == 0, "no store wired → empty (graceful), never a crash"
