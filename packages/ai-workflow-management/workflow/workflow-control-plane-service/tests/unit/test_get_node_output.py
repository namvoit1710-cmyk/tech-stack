"""Tests for GetNodeOutputUseCase — the node-output panel endpoint.

Covers one case per §9 contract row of
``docs/design/node-output-panel-api.md`` plus the edge states.
"""

import pytest

from app.layer1_domain.entities.data_reference import add_artifact, build_file_ref
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.task import Task
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
)
from app.layer2_application.features.get_node_output.use_cases.get_node_output_use_case import (
    GetNodeOutputCommand,
    GetNodeOutputUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_event_log_repo import (
    MemoryEventLogRepository,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)
from tests.conftest import DummyLogger


class _FakeFileClient:
    """Minimal file-service stub — returns the first ``$top`` rows."""

    def __init__(self, rows):
        self.rows = rows

    async def query_file_data(self, file_id, params):
        top = int(params.get("$top", 5))
        return {"data": self.rows[:top]}


def _build_uc(file_client=None):
    run_repo = MemoryRunRepository()
    workflow_repo = MemoryWorkflowRepository()
    task_repo = MemoryTaskRepository()
    event_log = MemoryEventLogRepository()
    uc = GetNodeOutputUseCase(
        logger=DummyLogger(),
        run_repo=run_repo,
        workflow_repo=workflow_repo,
        task_repo=task_repo,
        event_log_repo=event_log,
        file_service_client=file_client,
    )
    return uc, run_repo, workflow_repo, task_repo, event_log


def _seed_workflow_run(workflow_repo, run_repo, node, *, skipped=None):
    wf = Workflow(id="wf-1", name="WF", nodes=[node], edges=[])
    workflow_repo.save(wf)
    run = WorkflowRun(id="r1", workflow_id="wf-1", status=RunStatus.RUNNING)
    if skipped:
        run.context.skipped_nodes = set(skipped)
    run_repo.save(run)
    return run


def _emit(event_log, node_id, value, *, output_port="", taken_edge_ports=None, ts="2026-05-19T05:00:00+00:00"):
    event_log.append(EventLogEnvelope(
        event_id=f"evt-{node_id}-{event_log.list_by_run('r1').__len__()}",
        run_id="r1", topic="node.output.produced",
        event_type="NodeOutputProduced", timestamp=ts,
        payload={
            "node_id": node_id,
            "state_updates": {node_id: value},
            "output_port": output_port,
            "taken_edge_ports": taken_edge_ports or {},
        },
    ))


def _task(node_id, status, *, iter_=None, error=None):
    scope_stack = None
    if iter_ is not None:
        scope_stack = [LoopScope(
            loop_node_id="loop-x", invocation_id="inv-1", iter=iter_, total=3,
        )]
    return Task(
        run_id="r1", node_id=node_id, status=status,
        scope_stack=scope_stack, error=error,
        started_at="2026-05-19T05:00:00+00:00",
        completed_at="2026-05-19T05:00:01+00:00",
    )


# ---------------------------------------------------------------------------
# TASK
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_task_single_run_one_iteration():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    _emit(log, "task-1", {"item": "alpha", "score": 5})

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    assert r.error is None
    assert r.node_type == "TASK"
    assert len(r.iterations) == 1
    it = r.iterations[0]
    assert it.status == "COMPLETED"
    assert it.task_id is not None
    paths = {p.path: p for p in it.scopes[0].paths}
    assert paths["score"].sample == 5
    assert paths["score"].display_path == "{{$task-1.score}}"


@pytest.mark.asyncio
async def test_loop_body_multiple_iterations_in_order():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="body-1", name="Enrich", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    for i, item in enumerate(["alpha", "beta", "gamma"]):
        task_repo.save(_task("body-1", TaskStatus.COMPLETED, iter_=i))
        _emit(log, "body-1", {"item": item})

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="body-1"))
    assert [it.iteration_index for it in r.iterations] == [0, 1, 2]
    samples = [
        {p.path: p.sample for p in it.scopes[0].paths} for it in r.iterations
    ]
    assert [s["item"] for s in samples] == ["alpha", "beta", "gamma"]


@pytest.mark.asyncio
async def test_file_backed_output_preview_rows_capped_at_5():
    rows = [{"sku": f"S{i}", "qty": i} for i in range(20)]
    uc, run_repo, wf_repo, task_repo, log = _build_uc(
        file_client=_FakeFileClient(rows))
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    _emit(log, "task-1", build_file_ref(
        file_id="f-1", content_type="text/csv", row_count=20, columns=["sku", "qty"]))

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    scope = r.iterations[0].scopes[0]
    assert scope.has_file_data is True
    assert scope.file_id == "f-1"
    assert len(scope.preview_rows) == 5          # bounded preview
    assert scope.preview_rows[0] == {"sku": "S0", "qty": 0}


@pytest.mark.asyncio
async def test_nested_file_ref_field_renders_as_file_backed_path():
    """SA-1905 C2: a code node streams its large ``result`` array to CSV, so
    the output is ``{result: <file_ref>, logs: [...]}`` — a plain dict whose
    ``result`` FIELD is a file_ref. The Output panel must present ``result``
    as a ``file_ref`` path (columns + preview rows + expandable), not a raw
    scalar object the FE fails to parse as an array. Non-file fields stay
    scalar."""
    rows = [{"i": i, "sku": f"SKU-{i}"} for i in range(9)]
    uc, run_repo, wf_repo, task_repo, log = _build_uc(
        file_client=_FakeFileClient(rows))
    node = WorkflowNode(id="gen", name="Generate", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("gen", TaskStatus.COMPLETED))
    result_ref = build_file_ref(
        file_id="f-res", content_type="text/csv", row_count=60000,
        columns=["i", "sku"],
    )
    _emit(log, "gen", {"result": result_ref, "logs": ["done"]})

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="gen"))
    paths = {p.path: p for p in r.iterations[0].scopes[0].paths}

    # ``result`` is a file-backed, expandable path with real columns + rows.
    assert "result" in paths
    assert paths["result"].kind == "file_ref"
    assert paths["result"].columns == ["i", "sku"]
    assert paths["result"].expandable is True
    assert len(paths["result"].preview_rows) == 5     # bounded preview
    assert paths["result"].preview_rows[0] == {"i": 0, "sku": "SKU-0"}
    # Non-file fields stay plain scalars.
    assert paths["logs"].kind == "scalar"


@pytest.mark.asyncio
async def test_file_backed_output_backfills_scalar_samples_from_odata():
    """No-``extracted`` model: a file-backed output carries no inline scalars,
    so per-path samples are backfilled on demand from the first OData row."""
    uc, run_repo, wf_repo, task_repo, log = _build_uc(
        file_client=_FakeFileClient([{"sku": "S0", "qty": 7}]))
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    _emit(log, "task-1", build_file_ref(
        file_id="f-1", content_type="text/csv", row_count=1,
        columns=["sku", "qty"]))

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    paths = {p.path: p for p in r.iterations[0].scopes[0].paths}
    assert paths["sku"].sample == "S0"
    assert paths["qty"].sample == 7
    # display_path stays finalized after the on-demand backfill.
    assert paths["sku"].display_path == "{{$task-1.sku}}"


@pytest.mark.asyncio
async def test_file_backed_output_surfaces_uncovered_columns_from_odata():
    """When the file_ref carries no ``columns`` metadata, the on-demand OData
    read still surfaces the CSV's columns as sampled, finalized scalar paths."""
    uc, run_repo, wf_repo, task_repo, log = _build_uc(
        file_client=_FakeFileClient([{"region": "EU", "count": 3}]))
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    _emit(log, "task-1", build_file_ref(
        file_id="f-1", content_type="text/csv", row_count=1, columns=[]))

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    paths = {p.path: p for p in r.iterations[0].scopes[0].paths}
    assert paths["region"].sample == "EU"
    assert paths["count"].sample == 3
    assert paths["region"].display_path == "{{$task-1.region}}"


@pytest.mark.asyncio
async def test_file_backed_output_no_file_client_keeps_preview_empty():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()  # no file client
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    _emit(log, "task-1", build_file_ref(
        file_id="f-1", content_type="text/csv", row_count=20, columns=["sku"]))

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    assert r.iterations[0].scopes[0].preview_rows == []


@pytest.mark.asyncio
async def test_file_producing_node_emits_artifacts_scope():
    """A node whose output carries artifacts gets a second scope of
    scope_type='artifacts' — same multi-scope shape as variable-suggestions."""
    uc, run_repo, wf_repo, task_repo, log = _build_uc(
        file_client=_FakeFileClient([{"sku": "A"}, {"sku": "B"}]))
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    fref = build_file_ref(file_id="f-root", content_type="text/csv",
                          row_count=0, columns=[])
    add_artifact(fref, file_id="f-orders", name="orders", artifact_id="orders")
    _emit(log, "task-1", fref)

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    scopes = r.iterations[0].scopes
    assert len(scopes) == 2
    assert scopes[0].scope_type == "node"
    art = scopes[1]
    assert art.scope_type == "artifacts"
    art_paths = {p.path: p for p in art.paths}
    assert art_paths["orders"].kind == "file_ref"
    assert art_paths["orders"].display_path == "{{$artifacts.orders}}"
    assert len(art_paths["orders"].preview_rows) == 2


@pytest.mark.asyncio
async def test_inline_output_has_no_artifacts_scope():
    """An inline (non-file) output yields only the node scope — no
    spurious artifacts scope."""
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.COMPLETED))
    _emit(log, "task-1", {"score": 5})

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    assert [s.scope_type for s in r.iterations[0].scopes] == ["node"]


@pytest.mark.asyncio
async def test_failed_task_status_only():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.FAILED, error="boom"))

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    assert len(r.iterations) == 1
    it = r.iterations[0]
    assert it.status == "FAILED"
    assert it.scopes == []
    assert it.error == "boom"


@pytest.mark.asyncio
async def test_failed_task_with_event_surfaces_body():
    # When a failed task has its failure-port NodeOutputProduced event, the
    # body must surface as a scope (not collapse to status-only).
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    t = _task("task-1", TaskStatus.FAILED, error="HTTP 500")
    t.output_metadata = {"error_code": "HTTP_500"}
    task_repo.save(t)
    _emit(log, "task-1", {"status": "FAILED", "error": "HTTP 500", "status_code": 500},
          output_port="failure")

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    assert len(r.iterations) == 1
    it = r.iterations[0]
    assert it.status == "FAILED"
    assert it.error == "HTTP 500"
    assert it.error_code == "HTTP_500"
    assert it.output_port == "failure"
    assert it.scopes, "failure body should surface as a scope"
    paths = {p.path: p for p in it.scopes[0].paths}
    assert paths["status_code"].sample == 500


@pytest.mark.asyncio
async def test_running_task_partial_iteration():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("task-1", TaskStatus.RUNNING))

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-1"))
    assert len(r.iterations) == 1
    assert r.iterations[0].status == "RUNNING"
    assert r.iterations[0].scopes == []


# ---------------------------------------------------------------------------
# LOOP
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_loop_node_expands_results():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="loop-1", name="For Each", node_type=NodeType.LOOP)
    _seed_workflow_run(wf_repo, run_repo, node)
    _emit(log, "loop-1", {
        "output": {"results": [
            {"index": 0, "item": "alpha", "output": {"score": 1}},
            {"index": 1, "item": "beta", "output": {"score": 2}},
        ]},
        "metadata": {"type": "loop", "total_iterations": 2, "reason": "all_done"},
    })

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="loop-1"))
    assert [it.iteration_index for it in r.iterations] == [0, 1]
    assert r.node_summary == {
        "type": "loop", "total_iterations": 2, "reason": "all_done",
    }
    scope0 = r.iterations[0].scopes[0]
    paths = {p.path: p for p in scope0.paths}
    assert paths["score"].sample == 1
    assert paths["score"].display_path == "{{$loop-1.results[0].output.score}}"


# ---------------------------------------------------------------------------
# Branching — CONDITION / SWITCH
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_condition_empty_output_with_taken_edges():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="cond-1", name="Condition", node_type=NodeType.CONDITION)
    _seed_workflow_run(wf_repo, run_repo, node)
    _emit(log, "cond-1", {"output": {}, "metadata": {"type": "condition"}},
          taken_edge_ports={"edge-ac": "true_branch"})

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="cond-1"))
    assert len(r.iterations) == 1
    it = r.iterations[0]
    assert it.task_id is None
    assert it.output_port == ""
    assert it.taken_edge_ports == {"edge-ac": "true_branch"}
    paths = {p.path: p for p in it.scopes[0].paths}
    assert paths["metadata.type"].sample == "condition"


# ---------------------------------------------------------------------------
# MERGE — below quorum
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_below_quorum_no_iterations():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="merge-1", name="Join", node_type=NodeType.MERGE)
    _seed_workflow_run(wf_repo, run_repo, node)

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="merge-1"))
    assert r.iterations == []
    assert r.error is None


# ---------------------------------------------------------------------------
# PENDING — INPUT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_node_awaiting_then_completed():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="input-1", name="Collect", node_type=NodeType.INPUT)
    _seed_workflow_run(wf_repo, run_repo, node)

    # Awaiting input: a PENDING task, dispatch scaffold event.
    pending = _task("input-1", TaskStatus.PENDING)
    task_repo.save(pending)
    _emit(log, "input-1", {})
    r1 = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="input-1"))
    assert len(r1.iterations) == 1
    assert r1.iterations[0].status == "PENDING"
    assert r1.iterations[0].scopes == []

    # Resume: task flips to COMPLETED, a second event carries submitted data.
    pending.status = TaskStatus.COMPLETED
    task_repo.save(pending)
    _emit(log, "input-1", {"region": "EU", "threshold": 50})
    r2 = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="input-1"))
    assert len(r2.iterations) == 1
    assert r2.iterations[0].status == "COMPLETED"
    paths = {p.path: p.sample for p in r2.iterations[0].scopes[0].paths}
    assert paths["region"] == "EU"
    assert paths["threshold"] == 50


# ---------------------------------------------------------------------------
# WORKFLOW
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_workflow_node_bound_child_output():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="sub-1", name="Run Sub", node_type=NodeType.WORKFLOW)
    _seed_workflow_run(wf_repo, run_repo, node)
    task_repo.save(_task("sub-1", TaskStatus.COMPLETED))
    _emit(log, "sub-1", {
        "output": {"total": 4210},
        "metadata": {"child_workflow_id": "wf-child-88"},
    })

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="sub-1"))
    assert len(r.iterations) == 1
    paths = {p.path: p.sample for p in r.iterations[0].scopes[0].paths}
    assert paths["output.total"] == 4210
    assert paths["metadata.child_workflow_id"] == "wf-child-88"


# ---------------------------------------------------------------------------
# SKIPPED + errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skipped_node_status_only():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="cond-2", name="Condition", node_type=NodeType.CONDITION)
    _seed_workflow_run(wf_repo, run_repo, node, skipped=["cond-2"])

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="cond-2"))
    assert len(r.iterations) == 1
    assert r.iterations[0].status == "SKIPPED"
    assert r.iterations[0].scopes == []


@pytest.mark.asyncio
async def test_never_reached_node_empty_iterations():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-9", name="Late", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="task-9"))
    assert r.iterations == []
    assert r.error is None


@pytest.mark.asyncio
async def test_run_not_found():
    uc, *_ = _build_uc()
    r = await uc.execute(GetNodeOutputCommand(run_id="nope", node_id="x"))
    assert r.error_code == "RUN_NOT_FOUND"
    assert r.iterations == []


@pytest.mark.asyncio
async def test_node_not_found():
    uc, run_repo, wf_repo, task_repo, log = _build_uc()
    node = WorkflowNode(id="task-1", name="Load", node_type=NodeType.TASK)
    _seed_workflow_run(wf_repo, run_repo, node)

    r = await uc.execute(GetNodeOutputCommand(run_id="r1", node_id="ghost"))
    assert r.error_code == "NODE_NOT_FOUND"
