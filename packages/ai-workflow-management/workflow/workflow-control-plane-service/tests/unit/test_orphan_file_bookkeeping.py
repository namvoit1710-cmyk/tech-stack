"""SA-1910 — files nobody points at get deleted, and the ones that are, do not.

Moving the CSV-tree upload into the worker created a new way to waste storage:
the worker builds N files, and whether any of them survives is decided later by
`keep_inline`, `output_binding` and the rest of `complete_task` — none of which
the worker can see. So it reports what it created and the control plane deletes
what nothing ends up pointing at.

The scope is stated in the code and asserted here: this covers the callback
ARRIVING and the ref not surviving. A callback that never arrives takes the list
with it, and only a sweeper can find those files. Nothing here pretends
otherwise.
"""
from __future__ import annotations

import pytest

from app.layer1_domain.entities.data_reference import build_file_ref, collect_file_ids
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext, GraphTopology
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import (
    CompleteTaskCommand, CompleteTaskUseCase,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_stage_history_repo import (
    MemoryStageHistoryRepository,
)
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger, DummyOrchestrationEngine


# ---------------------------------------------------------------------------
# 1. "unreferenced" is computed, not guessed
# ---------------------------------------------------------------------------

def test_collect_walks_refs_envelopes_lists_and_sidecars():
    value = {
        "metadata": {},
        "result": {
            "__file_ref": True, "file_id": "root", "arrow_file_id": "sidecar",
            "artifacts": [
                {"file_id": "child", "artifacts": [{"file_id": "grandchild"}]},
            ],
        },
        "extras": [{"file_id": "in_a_list"}],
    }
    assert collect_file_ids(value) == {
        "root", "sidecar", "child", "grandchild", "in_a_list",
    }


@pytest.mark.parametrize("value", [None, {}, [], "text", 7, {"file_id": ""}, {"file_id": None}])
def test_collect_is_empty_when_there_is_nothing_to_find(value):
    assert collect_file_ids(value) == set()


def test_collect_does_not_run_away_on_a_deep_structure():
    node: dict = {"file_id": "deep"}
    for _ in range(200):
        node = {"nested": node}
    # bounded rather than recursing to a RecursionError; the outer levels hold
    # nothing, so finding nothing is the correct answer for this shape
    assert collect_file_ids(node) == set()


# ---------------------------------------------------------------------------
# 2. gc_files
# ---------------------------------------------------------------------------

class _CountingClient(MockFileServiceClient):
    def __init__(self, *, raises: bool = False) -> None:
        super().__init__()
        self.deleted: list[str] = []
        self._raises = raises

    async def delete_file(self, file_id: str):
        self.deleted.append(file_id)
        if self._raises:
            raise RuntimeError("file service down")
        return await super().delete_file(file_id)


def _store(client=None, **kw) -> DataStoreService:
    return DataStoreService(client or _CountingClient(), CsvConverter(), **kw)


@pytest.mark.asyncio
async def test_gc_deletes_each_id_once_and_ignores_blanks():
    c = _CountingClient()
    await _store(c).gc_files(["a", "a", "", None, "b"], reason="test")
    assert c.deleted == ["a", "b"]


@pytest.mark.asyncio
async def test_gc_is_a_no_op_when_disabled():
    """Same kill switch `edit_node_data` uses, so a debugging session that wants
    every intermediate to stay queryable turns off one thing, not two."""
    c = _CountingClient()
    await _store(c, orphan_gc_enabled=False).gc_files(["a"], reason="test")
    assert c.deleted == []


@pytest.mark.asyncio
async def test_gc_never_raises():
    """Its callers are already on a failure path. A delete that raises there
    turns wasted storage into a lost run."""
    c = _CountingClient(raises=True)
    await _store(c).gc_files(["a", "b"], reason="test")
    assert c.deleted == ["a", "b"]      # both attempted, neither propagated


# ---------------------------------------------------------------------------
# 3. the native split cleans up after itself before falling back
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_native_split_that_dies_part_way_deletes_what_it_uploaded():
    """The Python writer re-uploads the WHOLE tree, so without this the
    half-tree stays on the file service forever with nothing pointing at it."""
    pytest.importorskip("json_csv_streamer")
    import json_csv_streamer as streamer
    if not hasattr(streamer, "jsonl_split_recursive"):
        pytest.skip("native wheel predates jsonl_split_recursive")

    c = _CountingClient()
    store = _store(c, recursive_split=True, native_split=True)

    calls = {"n": 0}
    real = store._upload_csv

    async def _flaky(csv_bytes, filename):
        calls["n"] += 1
        if calls["n"] == 2:                       # the child
            raise RuntimeError("upload refused")
        return await real(csv_bytes, filename)

    store._upload_csv = _flaky
    data = {"title": "t", "rows": [{"a": 1}, {"a": 2}]}
    ref = await store.store_data("k", data)

    assert c.deleted, "the partial native tree was left behind"
    # and the value still stored, via the Python writer
    assert [a["name"] for a in ref["artifacts"]] == ["rows"]
    assert ref["file_id"] not in c.deleted, "the surviving tree must not be GC'd"


@pytest.mark.asyncio
async def test_a_successful_native_split_deletes_nothing():
    pytest.importorskip("json_csv_streamer")
    import json_csv_streamer as streamer
    if not hasattr(streamer, "jsonl_split_recursive"):
        pytest.skip("native wheel predates jsonl_split_recursive")

    c = _CountingClient()
    store = _store(c, recursive_split=True, native_split=True)
    await store.store_data("k", {"title": "t", "rows": [{"a": 1}]})
    assert c.deleted == []


# ---------------------------------------------------------------------------
# 4. complete_task: pop the key, delete what the output does not reference
# ---------------------------------------------------------------------------

class _RecordingStore:
    """A data store that file-backs into a ref of our choosing and records GC."""

    def __init__(self, ref=None):
        self.calls: list[tuple[str, object]] = []
        self.gc: list[str] = []
        self._ref = ref or build_file_ref(file_id="kept", row_count=1, columns=["a"])

    async def store_data(self, node_id, data):
        self.calls.append((node_id, data))
        return self._ref

    async def gc_files(self, file_ids, *, reason=""):
        self.gc.extend(file_ids)


def _seed(workflow_repo, run_repo, task_repo, worker_type="http_request"):
    workflow = Workflow(
        id="wf", name="wf",
        nodes=[
            WorkflowNode(id="t", name="Trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="gen", name="gen", node_type=NodeType.TASK, worker_type=worker_type),
        ],
        edges=[WorkflowEdge(source_node_id="t", target_node_id="gen")],
    )
    workflow_repo.save(workflow)
    run_repo.save(WorkflowRun(
        id="r1", workflow_id="wf", status=RunStatus.RUNNING,
        context=RunContext(
            topology=GraphTopology(
                adjacency={"t": ["gen"], "gen": []},
                predecessors={"t": [], "gen": ["t"]},
                edge_map={"t": workflow.edges, "gen": []},
                entry_nodes=["t"], leaf_nodes=["gen"],
            ),
            completed_nodes={"t"}, variables={"t": {"output": {}}},
        ),
    ))
    task_repo.save(Task(
        id="r1-gen", run_id="r1", node_id="gen",
        status=TaskStatus.RUNNING, worker_type=worker_type,
    ))


async def _complete(output: dict, store: _RecordingStore, worker_type="http_request"):
    wf, run, task = MemoryWorkflowRepository(), MemoryRunRepository(), MemoryTaskRepository()
    _seed(wf, run, task, worker_type)
    uc = CompleteTaskUseCase(
        logger=DummyLogger(), workflow_repo=wf, run_repo=run, task_repo=task,
        orchestration_engine=DummyOrchestrationEngine(),
        stage_recorder=StageRecorder(repository=MemoryStageHistoryRepository()),
        data_store_service=store,
    )
    await uc.execute(CompleteTaskCommand(
        run_id="r1", task_id="r1-gen", output_reference="", output_metadata=output,
    ))
    return store


@pytest.mark.asyncio
async def test_the_key_never_reaches_store_data():
    """Popped BEFORE store_data, or `_uploaded_file_ids` becomes a column in the
    node's CSV and then a field of its value."""
    store = await _complete(
        {"result": {"a": 1}, "_uploaded_file_ids": ["kept"]}, _RecordingStore(),
    )
    assert store.calls, "the output should have been file-backed"
    _node_id, data = store.calls[0]
    assert "_uploaded_file_ids" not in data


@pytest.mark.asyncio
async def test_files_the_output_does_not_point_at_are_deleted():
    store = await _complete(
        {"result": {"a": 1}, "_uploaded_file_ids": ["kept", "dropped-1", "dropped-2"]},
        _RecordingStore(),   # stores into a ref whose file_id is "kept"
    )
    assert sorted(store.gc) == ["dropped-1", "dropped-2"]


@pytest.mark.asyncio
async def test_a_file_the_output_does_point_at_survives():
    store = await _complete(
        {"result": {"a": 1}, "_uploaded_file_ids": ["kept"]}, _RecordingStore(),
    )
    assert store.gc == []


@pytest.mark.asyncio
async def test_a_sidecar_counts_as_referenced():
    """A sidecar is a file somebody pays for, and it is reachable exactly as
    long as its ref is — deleting it would silently disable the fast read."""
    ref = build_file_ref(file_id="kept", row_count=1, columns=["a"])
    ref["arrow_file_id"] = "sidecar"
    store = await _complete(
        {"result": {"a": 1}, "_uploaded_file_ids": ["kept", "sidecar"]},
        _RecordingStore(ref),
    )
    assert store.gc == []


@pytest.mark.asyncio
async def test_nothing_is_deleted_when_the_worker_reported_nothing():
    store = await _complete({"result": {"a": 1}}, _RecordingStore())
    assert store.gc == []
