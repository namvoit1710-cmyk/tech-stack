"""Regression for bug #12 — gated PARALLEL + MERGE(wait_all) must not hang.

Topology: TRIGGER → PARALLEL → [compute_a (ON), compute_b (OFF)] → MERGE(all) → final

PARALLEL gates OFF ``compute_b`` (its braced-boolean condition resolves
falsy). Before the fix the gated-off branch was neither dispatched nor
skipped, so the MERGE with ``wait_all`` waited forever on a predecessor that
never arrived → the run stayed RUNNING permanently (the reported "parallel
doesn't work"). The fix marks the gated-off branch's nodes skipped (the
MERGE counts skipped predecessors toward quorum) and emits
``NodeCancelled(reason="branch_not_taken")`` so the projector/FE agree.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import RunContext, WorkflowRun
from app.layer1_domain.events import EventTopic
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.parallel_handler import ParallelNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.resolve.variable_resolver import (
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    StartRunAction,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer2_application.orchestration_queue.memory_queue import MemoryOrchestrationQueue
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import MemoryWorkflowRepository


class _CapturingPublisher:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def publish(self, topic, event):
        self.events.append((topic, event))

    def publish_with_headers(self, topic, event, *, headers=None, key=None):
        self.events.append((topic, event))

    def publish_many(self, items):
        for entry in items:
            if isinstance(entry, tuple) and len(entry) == 2:
                self.events.append((entry[0], entry[1]))

    def events_of(self, topic: str) -> list[object]:
        return [e for t, e in self.events if t == topic]


def _build_harness(workflow: Workflow):
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)
    resolver = VariableResolver(strategies=[
        FileBackedResolverStrategy(data_store, file_client=file_client),
        MetadataResolverStrategy(),
        TraversalResolverStrategy(),
    ])
    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.PARALLEL, ParallelNodeHandler(resolver))
    registry.register(NodeType.MERGE, MergeNodeHandler())
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    task_factory = TaskFactory()
    successor_processor = SuccessorProcessor(registry, task_factory, data_store_service=data_store)
    run_repo = MemoryRunRepository()
    task_repo = MemoryTaskRepository()
    workflow_repo = MemoryWorkflowRepository()
    workflow_repo.save(workflow)
    stage_recorder = StageRecorder(MagicMock())
    stage_recorder.record = MagicMock()
    publisher = _CapturingPublisher()
    engine = OrchestrationEngine(
        handler_registry=registry,
        task_factory=task_factory,
        successor_processor=successor_processor,
        stage_recorder=stage_recorder,
        task_repo=task_repo,
        run_repo=run_repo,
        event_publisher=publisher,
        file_service_client=file_client,
        csv_converter=csv_converter,
        workflow_repo=workflow_repo,
        variable_resolver=resolver,
        data_store_service=data_store,
    )
    queue = MemoryOrchestrationQueue()
    engine.set_queue(queue)
    return {"engine": engine, "queue": queue, "publisher": publisher,
            "run_repo": run_repo, "task_repo": task_repo}


async def _drive_until_idle(engine, queue, *, max_steps=80, idle_timeout=0.05) -> int:
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=idle_timeout)
        except asyncio.TimeoutError:
            return steps
        steps += 1
        if isinstance(action, StartRunAction):
            await engine.orchestrate_start(action.run_id)
        elif isinstance(action, ExecuteNodeAction):
            await engine.orchestrate_execute_node(action.run_id, action.task_id)
        else:
            raise AssertionError(f"unexpected action: {type(action).__name__}")
    raise AssertionError(f"queue did not drain within {max_steps} steps")


def _gated_parallel_workflow() -> Workflow:
    return Workflow(
        id="wf-gated", name="gated", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(
                id="parallel", node_type=NodeType.PARALLEL,
                config={"branches": [
                    {"nodes": ["compute_a"], "condition": "{{$trigger.body.flag_a}}"},  # ON
                    {"nodes": ["compute_b"], "condition": "{{$trigger.body.flag_b}}"},  # OFF
                ]},
            ),
            WorkflowNode(id="compute_a", node_type=NodeType.COMPUTE, config={"expression": "1"}),
            WorkflowNode(id="compute_b", node_type=NodeType.COMPUTE, config={"expression": "2"}),
            WorkflowNode(id="merge", node_type=NodeType.MERGE, config={"merge_strategy": "wait_all"}),
            WorkflowNode(id="final", node_type=NodeType.COMPUTE, config={"expression": "3"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="parallel"),
            WorkflowEdge(id="e2", source_node_id="parallel", target_node_id="compute_a"),
            WorkflowEdge(id="e3", source_node_id="parallel", target_node_id="compute_b"),
            WorkflowEdge(id="e4", source_node_id="compute_a", target_node_id="merge"),
            WorkflowEdge(id="e5", source_node_id="compute_b", target_node_id="merge"),
            WorkflowEdge(id="e6", source_node_id="merge", target_node_id="final"),
        ],
    )


@pytest.mark.asyncio
async def test_gated_parallel_with_wait_all_merge_completes():
    workflow = _gated_parallel_workflow()
    harness = _build_harness(workflow)
    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id="run-gated", workflow_id=workflow.id, status=RunStatus.RUNNING,
        context=RunContext(
            topology=topology,
            variables={"trigger": {"body": {"flag_a": True, "flag_b": False}}},
        ),
    )
    harness["run_repo"].save(run)

    await harness["engine"].orchestrate_start("run-gated")
    await _drive_until_idle(harness["engine"], harness["queue"])

    final_run = harness["run_repo"].get_by_id("run-gated")
    tasks = harness["task_repo"].list_by_run("run-gated")
    nodes_with_task = {t.node_id for t in tasks}

    # The run COMPLETES — no hang (the regression: it used to stay RUNNING).
    assert final_run.status == RunStatus.COMPLETED, (
        f"gated PARALLEL + MERGE(wait_all) hung: run status {final_run.status}. "
        f"The gated-off branch must be skipped so MERGE reaches quorum."
    )

    # Gated-OFF branch never dispatched, but is recorded skipped.
    assert "compute_b" not in nodes_with_task, "gated-off branch must not be dispatched"
    assert "compute_b" in final_run.context.skipped_nodes

    # Gated-ON branch + merge + final all completed.
    assert "compute_a" in final_run.context.completed_nodes
    assert "merge" in final_run.context.completed_nodes
    assert "final" in final_run.context.completed_nodes
    merge_task = [t for t in tasks if t.node_id == "merge"]
    assert merge_task and merge_task[0].status == TaskStatus.COMPLETED

    # A NodeCancelled(reason="branch_not_taken") is emitted for the gated-off
    # branch so the projector view and FE canvas mark it skipped.
    cancelled = harness["publisher"].events_of(EventTopic.NODE_CANCELLED)
    skipped_node_ids = {
        e.node_id for e in cancelled
        if getattr(e, "reason", "") == "branch_not_taken"
    }
    assert "compute_b" in skipped_node_ids, (
        "expected NodeCancelled(branch_not_taken) for the gated-off branch"
    )
