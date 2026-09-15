"""Unit tests for GetNodeDataUseCase."""

import pytest

from app.layer1_domain.entities.data_reference import (
    add_artifact, build_file_ref, register_artifacts,
)
from app.layer1_domain.entities.run_context_snapshot import RunContextSnapshot
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.value_objects import NodeType, RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.features.get_node_data.use_cases.get_node_data_use_case import (
    GetNodeDataCommand,
    GetNodeDataUseCase,
)
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository
from tests.conftest import DummyLogger
from tests.helpers.view_store_doubles import LiveViewStoreDouble


def _build_uc():
    run_repo = MemoryRunRepository()
    uc = GetNodeDataUseCase(
        logger=DummyLogger(),
        run_context_view_store=LiveViewStoreDouble(run_repo),
        task_repo=MemoryTaskRepository(),
        workflow_repo=MemoryWorkflowRepository(),
    )
    # Attach the run_repo so `_inject` can populate state via the
    # aggregate; ``LiveViewStoreDouble`` projects it through the view
    # for the use case.
    uc._test_run_repo = run_repo
    return uc


def _inject(uc, run=None, workflow=None, tasks=()):
    if workflow is not None:
        uc.workflow_repo.save(workflow)
    if run is not None:
        uc._test_run_repo.save(run)
    for t in tasks:
        uc.task_repo.save(t)


def _wf(node_type=NodeType.DATA_EDIT):
    return Workflow(
        id="wf-1", name="T",
        nodes=[WorkflowNode(id="n1", name="N1", node_type=node_type, config={})],
        edges=[],
    )


def _file_ref_with_scalars_and_table():
    ref = build_file_ref(
        file_id="f_main",
        columns=["customer_name", "total"],
        version=3,
    )
    orders = add_artifact(
        ref, file_id="f_orders", name="orders", artifact_id="orders",
        artifact_type="array_field",
    )
    orders["columns"] = ["__row_id", "sku", "qty"]
    return ref


def _mk_task(resolved_input=None):
    t = Task(
        id="t-1", run_id="r-1", node_id="n1",
        worker_type="", status=TaskStatus.PENDING,
        created_at="2026-01-01T00:00:00Z",
    )
    t.resolved_input = resolved_input
    t.output_metadata = {}
    return t


def _mk_run(variables=None):
    return WorkflowRun(
        id="r-1", workflow_id="wf-1",
        status=RunStatus.RUNNING,
        context=RunContext(variables=variables or {}),
    )


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_side_input_returns_resolved_input_as_data():
    uc = _build_uc()
    input_ref = _file_ref_with_scalars_and_table()
    _inject(uc, run=_mk_run(), workflow=_wf(), tasks=[_mk_task(resolved_input=input_ref)])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="input"))

    assert r.error is None
    assert r.data is not None
    assert r.output is None
    assert r.data.version == 3
    assert r.data.root["artifact_id"] == "__root__"
    assert r.data.root["file_id"] == "f_main"
    assert len(r.data.root["children"]) == 1
    assert r.data.root["children"][0]["artifact_id"] == "orders"


@pytest.mark.asyncio
async def test_side_output_returns_variables_working_copy():
    uc = _build_uc()
    output_ref = _file_ref_with_scalars_and_table()
    run = _mk_run(variables={"n1": output_ref})
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task()])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="output"))

    assert r.data is None
    assert r.output is not None
    assert r.output.root["file_id"] == "f_main"


@pytest.mark.asyncio
async def test_side_output_carries_output_port_when_set():
    """Output panel should show which port the last emission took."""
    uc = _build_uc()
    output_ref = _file_ref_with_scalars_and_table()
    run = _mk_run(variables={"n1": output_ref})
    run.context.last_output_ports["n1"] = "failure"
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task()])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="output"))

    assert r.output is not None
    assert r.output.output_port == "failure"
    assert r.output.taken_edge_ports is None


@pytest.mark.asyncio
async def test_side_output_carries_taken_edge_ports_for_branching_node():
    """CONDITION/SWITCH — the branch decision surfaces in output."""
    uc = _build_uc()
    output_ref = _file_ref_with_scalars_and_table()
    run = _mk_run(variables={"n1": output_ref})
    run.context.last_output_ports["n1"] = "true"
    run.context.last_taken_edge_ports["n1"] = {"e1": "true_branch", "e2": "false_branch"}
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task()])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="output"))

    assert r.output is not None
    assert r.output.output_port == "true"
    assert r.output.taken_edge_ports == {"e1": "true_branch", "e2": "false_branch"}


@pytest.mark.asyncio
async def test_side_input_never_carries_output_port():
    """output_port is an output-side concept; input payload must stay clean."""
    uc = _build_uc()
    input_ref = _file_ref_with_scalars_and_table()
    run = _mk_run()
    run.context.last_output_ports["n1"] = "success"
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task(resolved_input=input_ref)])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="input"))

    assert r.data is not None
    assert r.data.output_port is None
    assert r.data.taken_edge_ports is None


@pytest.mark.asyncio
async def test_side_output_leaves_port_none_when_never_emitted():
    """Nodes that never produced output should return None (not empty string)."""
    uc = _build_uc()
    output_ref = _file_ref_with_scalars_and_table()
    run = _mk_run(variables={"n1": output_ref})
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task()])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="output"))

    assert r.output is not None
    assert r.output.output_port is None
    assert r.output.taken_edge_ports is None


@pytest.mark.asyncio
async def test_side_both_returns_input_and_output():
    uc = _build_uc()
    input_ref = _file_ref_with_scalars_and_table()
    output_ref = _file_ref_with_scalars_and_table()
    output_ref["file_id"] = "f_main_v2"  # working copy diverged
    run = _mk_run(variables={"n1": output_ref})
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task(resolved_input=input_ref)])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="both"))

    assert r.data is not None and r.output is not None
    assert r.data.root["file_id"] == "f_main"
    assert r.output.root["file_id"] == "f_main_v2"
    # ETags differ because file_ids differ.
    assert r.data.etag != r.output.etag


@pytest.mark.asyncio
async def test_descriptor_never_carries_row_count():
    uc = _build_uc()
    ref = _file_ref_with_scalars_and_table()
    ref["row_count"] = 5000  # set on entity; descriptor must still omit
    _inject(uc, run=_mk_run(), workflow=_wf(), tasks=[_mk_task(resolved_input=ref)])

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1"))
    assert "row_count" not in r.data.root
    for child in r.data.root["children"]:
        assert "row_count" not in child


@pytest.mark.asyncio
async def test_etag_changes_when_any_descendant_file_id_changes():
    uc = _build_uc()
    run = _mk_run()
    ref_a = _file_ref_with_scalars_and_table()
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task(resolved_input=ref_a)])
    r_a = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="input"))

    ref_b = _file_ref_with_scalars_and_table()
    ref_b["artifacts"][0]["file_id"] = "f_orders_v2"
    uc.task_repo.save(_mk_task(resolved_input=ref_b))
    r_b = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="input"))

    assert r_a.data.etag != r_b.data.etag
    assert r_a.etag != r_b.etag


@pytest.mark.asyncio
async def test_schema_ref_points_at_schema_url():
    uc = _build_uc()
    _inject(uc, run=_mk_run(), workflow=_wf(), tasks=[_mk_task()])
    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1"))
    assert r.schema_ref["url"] == "/runs/r-1/nodes/n1/schema"
    assert r.schema_ref["etag"].startswith('W/"')


@pytest.mark.asyncio
async def test_produced_artifacts_filters_registry_by_node():
    uc = _build_uc()
    run = _mk_run()
    # Simulate three named artifacts produced by different nodes.
    run.context.variables["artifacts"] = {
        "mine_a": {"file_id": "f1", "produced_by_node": "n1",
                   "produced_by_task": "t", "content_type": "text/csv",
                   "size_bytes": 0, "registered_at": ""},
        "mine_b": {"file_id": "f2", "produced_by_node": "n1",
                   "produced_by_task": "t", "content_type": "text/csv",
                   "size_bytes": 0, "registered_at": ""},
        "other":  {"file_id": "f3", "produced_by_node": "other",
                   "produced_by_task": "t", "content_type": "text/csv",
                   "size_bytes": 0, "registered_at": ""},
    }
    _inject(uc, run=run, workflow=_wf(), tasks=[_mk_task()])
    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1"))
    assert r.produced_artifacts == ["mine_a", "mine_b"]


@pytest.mark.asyncio
async def test_missing_input_returns_empty_payload_not_error():
    uc = _build_uc()
    # Task exists but has no resolved_input yet (e.g., newly-entered PENDING).
    _inject(uc, run=_mk_run(), workflow=_wf(), tasks=[_mk_task(resolved_input=None)])
    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="input"))
    assert r.error is None
    assert r.data is not None
    assert r.data.root is None


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_not_found():
    uc = _build_uc()
    r = await uc.execute(GetNodeDataCommand(run_id="missing", node_id="n1"))
    assert r.error_code == "RUN_NOT_FOUND"


@pytest.mark.asyncio
async def test_node_not_found():
    uc = _build_uc()
    _inject(uc, run=_mk_run(), workflow=_wf())
    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="nope"))
    assert r.error_code == "NODE_NOT_FOUND"


# ---------------------------------------------------------------------------
# View-store cutover regression (node-data-view-store-cutover.md)
# ---------------------------------------------------------------------------


class _FixedViewStore:
    """Minimal ``IRunContextViewStore`` returning a pre-built snapshot.

    Scoped to tests that need the view store and the aggregate to
    diverge — i.e. to prove a reader is consulting the view store,
    not ``run_repo``.
    """

    def __init__(self, snapshot: RunContextSnapshot) -> None:
        self._snapshot = snapshot

    def get(self, run_id: str):
        return self._snapshot if self._snapshot.run_id == run_id else None

    def get_by_root_id(self, root_id: str):
        return [self._snapshot] if self._snapshot.root_run_id == root_id else []


def _snapshot(variables=None, last_output_ports=None, last_taken_edge_ports=None):
    from app.layer1_domain.entities.workflow_run import GraphTopology
    return RunContextSnapshot(
        run_id="r-1",
        workflow_id="wf-1",
        workflow_version="1",
        status="RUNNING",
        started_at="2026-01-01T00:00:00Z",
        completed_at=None,
        error=None,
        output_data=None,
        input_data={},
        variables=variables or {},
        last_output_ports=last_output_ports or {},
        last_taken_edge_ports=last_taken_edge_ports or {},
        completed_nodes=frozenset(),
        skipped_nodes=frozenset(),
        topology=GraphTopology(entry_nodes=[], edge_map={}, adjacency={}, predecessors={}),
        child_runs={},
        parent_run_id=None,
        root_run_id="r-1",
        depth_level=0,
    )


@pytest.mark.asyncio
async def test_side_output_reads_view_store_not_aggregate():
    """Phase 3b.2a removed the aggregate write from ``EditNodeDataUseCase``,
    so ``run.context.variables`` stays frozen at dispatch time while the
    projector-backed view store is the authoritative post-PATCH state.
    ``GET /data?side=output`` must read the view store or it will serve
    stale data forever.

    Setup a deliberate divergence — aggregate has OLD, view store has
    NEW — and assert the output reflects NEW."""
    old_ref = build_file_ref(
        file_id="f_old",
        columns=["name"],
        version=1,
    )
    new_ref = build_file_ref(
        file_id="f_new",
        columns=["name"],
        version=2,
    )

    run_repo = MemoryRunRepository()
    run_repo.save(_mk_run(variables={"n1": old_ref}))

    task_repo = MemoryTaskRepository()
    task_repo.save(_mk_task())

    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(_wf())

    view_store = _FixedViewStore(_snapshot(variables={"n1": new_ref}))

    uc = GetNodeDataUseCase(
        logger=DummyLogger(),
        run_context_view_store=view_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
    )

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="output"))

    assert r.error is None, r.error
    assert r.output is not None
    assert r.output.root["file_id"] == "f_new"
    assert r.output.version == 2


@pytest.mark.asyncio
async def test_side_output_port_and_edges_come_from_view_store():
    """``last_output_ports`` / ``last_taken_edge_ports`` are also on the
    view now (mirrored from ``NodeOutputProduced`` in the projector
    reducer). Reading from the aggregate would miss dispatcher-emitted
    port decisions post-3b.2a."""
    output_ref = _file_ref_with_scalars_and_table()
    run_repo = MemoryRunRepository()
    run_repo.save(_mk_run(variables={"n1": output_ref}))

    task_repo = MemoryTaskRepository()
    task_repo.save(_mk_task())

    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(_wf())

    view_store = _FixedViewStore(_snapshot(
        variables={"n1": output_ref},
        last_output_ports={"n1": "failure"},
        last_taken_edge_ports={"n1": {"e1": "false_branch"}},
    ))

    uc = GetNodeDataUseCase(
        logger=DummyLogger(),
        run_context_view_store=view_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
    )

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1", side="output"))

    assert r.output is not None
    assert r.output.output_port == "failure"
    assert r.output.taken_edge_ports == {"e1": "false_branch"}


@pytest.mark.asyncio
async def test_produced_artifacts_read_from_view_store():
    """The run-level ``$artifacts`` registry lives in ``variables['artifacts']``;
    the projector reducer rebuilds it from ``NodeOutputProduced`` write-sets.
    Post-cutover, ``_produced_artifacts`` must source it from the view."""
    artifacts_registry = {
        "mine_a": {"file_id": "f1", "produced_by_node": "n1",
                   "produced_by_task": "t", "content_type": "text/csv",
                   "size_bytes": 0, "registered_at": ""},
        "mine_b": {"file_id": "f2", "produced_by_node": "n1",
                   "produced_by_task": "t", "content_type": "text/csv",
                   "size_bytes": 0, "registered_at": ""},
        "other":  {"file_id": "f3", "produced_by_node": "other",
                   "produced_by_task": "t", "content_type": "text/csv",
                   "size_bytes": 0, "registered_at": ""},
    }

    run_repo = MemoryRunRepository()
    # Aggregate has NO registry — only the view store does.
    run_repo.save(_mk_run(variables={}))

    task_repo = MemoryTaskRepository()
    task_repo.save(_mk_task())

    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(_wf())

    view_store = _FixedViewStore(_snapshot(variables={"artifacts": artifacts_registry}))

    uc = GetNodeDataUseCase(
        logger=DummyLogger(),
        run_context_view_store=view_store,
        task_repo=task_repo,
        workflow_repo=workflow_repo,
    )

    r = await uc.execute(GetNodeDataCommand(run_id="r-1", node_id="n1"))
    assert r.produced_artifacts == ["mine_a", "mine_b"]
