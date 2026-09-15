"""MERGE wait_any / wait_n end-to-end fan-out — downstream must run exactly once.

The previously-untested strategies (``wait_any``, ``wait_n``) have no internal
fire-once guard and MERGE is exempt from the engine's task-creation dedup, so the
only thing keeping downstream from double-dispatching is the fan-out dedup
(``fanout.py``: don't route to an already-completed target) plus the downstream
node's own dedup. This drives the real engine and asserts the invariant:
``wait_any``/``wait_n`` complete and dispatch their (COMPUTE) downstream exactly
once. (``wait_all`` is already covered by test_gated_parallel_merge.py and
test_edge_based_engine_e2e.py.)

NOTE on ordering: this serial queue driver (and the production engine's per-run
asyncio.Lock) processes one action at a time, so MERGE fires on the FIRST arrival
and later predecessors' fan-out to it is skipped (already-completed) — observable
as ``merge task count == 1``. That's the SEQUENTIAL path. A true simultaneous
double-fire (two MERGE tasks both executing) is not reproduced here; the residual
risk it would pose — a MERGE whose successor is itself MERGE/PARALLEL (both
dedup-exempt) — is therefore NOT covered. Downstream-once is asserted only for a
non-exempt (COMPUTE) successor.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy, TraversalResolverStrategy, VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction, StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


def _harness(workflow: Workflow):
    fc = MockFileServiceClient(); cv = CsvConverter(); ds = DataStoreService(fc, cv)
    rz = VariableResolver(strategies=[
        FileBackedResolverStrategy(ds, file_client=fc),
        MetadataResolverStrategy(), TraversalResolverStrategy(),
    ])
    reg = NodeHandlerRegistry()
    reg.register(NodeType.TRIGGER, TriggerNodeHandler())
    reg.register(NodeType.MERGE, MergeNodeHandler())
    reg.register(NodeType.COMPUTE, ComputeNodeHandler(rz))
    tf = TaskFactory(); sp = SuccessorProcessor(reg, tf, data_store_service=ds)
    rr = MemoryRunRepository(); tr = MemoryTaskRepository(); wr = MemoryWorkflowRepository()
    wr.save(workflow)
    sr = StageRecorder(MagicMock()); sr.record = MagicMock()
    engine = OrchestrationEngine(
        handler_registry=reg, task_factory=tf, successor_processor=sp, stage_recorder=sr,
        task_repo=tr, run_repo=rr, event_publisher=MagicMock(), file_service_client=fc,
        csv_converter=cv, workflow_repo=wr, variable_resolver=rz, data_store_service=ds,
    )
    q = MemoryOrchestrationQueue(); engine.set_queue(q)
    return {"engine": engine, "queue": q, "rr": rr, "tr": tr}


async def _drive(engine, queue, max_steps=120, idle=0.05):
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=idle)
        except asyncio.TimeoutError:
            return
        steps += 1
        if isinstance(action, StartRunAction):
            await engine.orchestrate_start(action.run_id)
        elif isinstance(action, ExecuteNodeAction):
            await engine.orchestrate_execute_node(action.run_id, action.task_id)
    raise AssertionError("queue did not drain")


def _merge_workflow(strategy, preds, extra_cfg=None):
    cfg = {"merge_strategy": strategy}
    if extra_cfg:
        cfg.update(extra_cfg)
    nodes = [WorkflowNode(id="trigger", node_type=NodeType.TRIGGER)]
    edges = []
    for p in preds:
        nodes.append(WorkflowNode(id=p, node_type=NodeType.COMPUTE, config={"expression": "1"}))
        edges.append(WorkflowEdge(id=f"t-{p}", source_node_id="trigger", target_node_id=p))
        edges.append(WorkflowEdge(id=f"{p}-m", source_node_id=p, target_node_id="merge"))
    nodes.append(WorkflowNode(id="merge", node_type=NodeType.MERGE, config=cfg))
    nodes.append(WorkflowNode(id="final", node_type=NodeType.COMPUTE, config={"expression": "9"}))
    edges.append(WorkflowEdge(id="m-final", source_node_id="merge", target_node_id="final"))
    return Workflow(id=f"wf-{strategy}", name=strategy, version="1", nodes=nodes, edges=edges)


async def _run(strategy, preds, extra_cfg=None):
    wf = _merge_workflow(strategy, preds, extra_cfg)
    h = _harness(wf)
    topo = GraphTopology.from_workflow(wf)
    run = WorkflowRun(id=f"run-{strategy}", workflow_id=wf.id, status=RunStatus.RUNNING,
                      context=RunContext(topology=topo, variables={"trigger": {"body": {}}}))
    h["rr"].save(run)
    await h["engine"].orchestrate_start(run.id)
    await _drive(h["engine"], h["queue"])
    final_run = h["rr"].get_by_id(run.id)
    tasks = h["tr"].list_by_run(run.id)
    return final_run, tasks


def _node_task_count(tasks, node_id):
    return len([t for t in tasks if t.node_id == node_id])


@pytest.mark.asyncio
async def test_wait_any_completes_and_downstream_runs_once():
    final_run, tasks = await _run("wait_any", ["a", "b"])
    assert final_run.status == RunStatus.COMPLETED, "wait_any merge must not hang"
    # Invariant: the (COMPUTE) downstream of MERGE is dispatched exactly once.
    assert _node_task_count(tasks, "final") == 1, "downstream must run exactly once"
    assert "merge" in final_run.context.completed_nodes
    assert "final" in final_run.context.completed_nodes
    merge_tasks = [t for t in tasks if t.node_id == "merge"]
    assert merge_tasks and all(t.status == TaskStatus.COMPLETED for t in merge_tasks)


@pytest.mark.asyncio
async def test_wait_n_completes_and_downstream_runs_once():
    final_run, tasks = await _run("wait_n", ["a", "b", "c"], {"wait_count": 2})
    assert final_run.status == RunStatus.COMPLETED, "wait_n merge must not hang"
    assert _node_task_count(tasks, "final") == 1, "downstream must run exactly once"
    assert "merge" in final_run.context.completed_nodes
    assert "final" in final_run.context.completed_nodes


@pytest.mark.asyncio
async def test_wait_all_baseline_downstream_runs_once():
    # Control: the well-covered strategy, asserted here for parity.
    final_run, tasks = await _run("wait_all", ["a", "b"])
    assert final_run.status == RunStatus.COMPLETED
    assert _node_task_count(tasks, "final") == 1
