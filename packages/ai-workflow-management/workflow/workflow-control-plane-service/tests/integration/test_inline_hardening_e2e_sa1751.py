"""SA-1751 — end-to-end reproductions: build real workflows using the fixed
inline nodes, drive them through a real ``OrchestrationEngine``, and OBSERVE
that the audited bug no longer occurs.

Companion to the handler-level contract in
``tests/unit/test_inline_node_hardening_sa1751.py``. Where those pin the handler
outputs, these prove the behavior end-to-end through edge routing + the queue
drain:

  * J2 — a CONDITION whose expression can't resolve now FAILS the run loud
         (NodeFailed) instead of silently routing ``else`` to completion.
  * J3 — a CONDITION that dead-ends (chosen branch unwired) now emits a WARNING
         so the silent stall is observable; the run still completes.
  * J5 — a SWITCH legacy ``cases`` value matches type-aware (int 1 == float
         1.0) end-to-end, routing to the matched target.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import MagicMock

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.entities.workflow_run import (
    GraphTopology,
    RunContext,
    WorkflowRun,
)
from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.data.csv_converter import CsvConverter
from app.layer2_application.data.data_store_service import DataStoreService
from app.layer2_application.events.stage_recorder import StageRecorder
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.node_handlers.compute_handler import ComputeNodeHandler
from app.layer2_application.node_handlers.condition_handler import ConditionNodeHandler
from app.layer2_application.node_handlers.merge_handler import MergeNodeHandler
from app.layer2_application.node_handlers.switch_handler import SwitchNodeHandler
from app.layer2_application.node_handlers.trigger_handler import TriggerNodeHandler
from app.layer2_application.orchestration.orchestration_engine import OrchestrationEngine
from app.layer2_application.orchestration.successor_processor import SuccessorProcessor
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.orchestration_queue.memory_queue import (
    MemoryOrchestrationQueue,
)
from app.layer2_application.interfaces.orchestration_queue_interface import (
    ExecuteNodeAction,
    LoopIterationAction,
    StartRunAction,
)
from app.layer2_application.resolve.file_backed_resolver import FileBackedResolverStrategy
from app.layer2_application.resolve.variable_resolver import (
    ConditionEvaluator,
    MetadataResolverStrategy,
    TraversalResolverStrategy,
    VariableResolver,
)
from app.layer4_frameworks.clients.mock_file_service_client import MockFileServiceClient
from app.layer4_frameworks.persistence.memory.memory_run_repo import MemoryRunRepository
from app.layer4_frameworks.persistence.memory.memory_task_repo import MemoryTaskRepository
from app.layer4_frameworks.persistence.memory.memory_workflow_repo import (
    MemoryWorkflowRepository,
)


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
                self.events.append(entry)

    def events_of(self, topic: str) -> list[object]:
        return [e for t, e in self.events if t == topic]


def _build_harness(workflow: Workflow):
    file_client = MockFileServiceClient()
    csv_converter = CsvConverter()
    data_store = DataStoreService(file_client, csv_converter)
    resolver = VariableResolver(
        strategies=[
            FileBackedResolverStrategy(data_store, file_client=file_client),
            MetadataResolverStrategy(),
            TraversalResolverStrategy(),
        ],
    )
    registry = NodeHandlerRegistry()
    registry.register(NodeType.TRIGGER, TriggerNodeHandler())
    registry.register(NodeType.COMPUTE, ComputeNodeHandler(resolver))
    registry.register(NodeType.CONDITION, ConditionNodeHandler(ConditionEvaluator(resolver)))
    registry.register(NodeType.SWITCH, SwitchNodeHandler(variable_resolver=resolver))
    registry.register(NodeType.MERGE, MergeNodeHandler())

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
    return {"engine": engine, "queue": queue, "publisher": publisher, "run_repo": run_repo}


async def _drive_queue(engine, queue, *, max_steps: int = 200) -> int:
    steps = 0
    while steps < max_steps:
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=0.05)
        except asyncio.TimeoutError:
            return steps
        steps += 1
        if isinstance(action, StartRunAction):
            await engine.orchestrate_start(action.run_id)
        elif isinstance(action, ExecuteNodeAction):
            await engine.orchestrate_execute_node(action.run_id, action.task_id)
        elif isinstance(action, LoopIterationAction):
            await engine.orchestrate_loop_iteration(
                action.run_id, action.task_id, action.loop_node_id, action.iteration_index,
            )
        else:
            raise AssertionError(f"unexpected action type: {type(action).__name__}")
    raise AssertionError(f"queue did not drain within {max_steps} steps")


async def _start_and_drive(harness, workflow, run_id, variables=None):
    topology = GraphTopology.from_workflow(workflow)
    run = WorkflowRun(
        id=run_id, workflow_id=workflow.id, status=RunStatus.RUNNING,
        context=RunContext(topology=topology, variables=variables or {"input": {}}),
    )
    harness["run_repo"].save(run)
    await harness["engine"].orchestrate_start(run_id)
    await _drive_queue(harness["engine"], harness["queue"])
    return harness["run_repo"].get_by_id(run_id)


# ─────────────────────── J2 — CONDITION fail-loud e2e ────────────────────────


@pytest.mark.asyncio
async def test_condition_unresolvable_expression_fails_run():
    """A CONDITION referencing a missing variable used to resolve False and
    silently route ``else`` → run COMPLETED down the wrong branch. It now
    fails loud: the node emits NodeFailed and the run does not COMPLETE."""
    workflow = Workflow(
        id="wf-j2", name="cond-fail-loud", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="cond", node_type=NodeType.CONDITION,
                         config={"expression": "{{$missing.value}} > 5"}),
            WorkflowNode(id="then", node_type=NodeType.COMPUTE, config={"expression": "1"}),
            WorkflowNode(id="else", node_type=NodeType.COMPUTE, config={"expression": "2"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="cond"),
            WorkflowEdge(id="e_t", source_node_id="cond", target_node_id="then",
                         source_port_id="true_branch"),
            WorkflowEdge(id="e_f", source_node_id="cond", target_node_id="else",
                         source_port_id="false_branch"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-j2")

    failed = harness["publisher"].events_of(EventTopic.NODE_FAILED)
    assert any(e.node_id == "cond" for e in failed), "CONDITION must fail loud on resolution error"
    # Neither branch ran — the silent misroute is gone.
    completed = {e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)}
    assert "then" not in completed and "else" not in completed
    assert run.status != RunStatus.COMPLETED


# ─────────────────────── J3 — CONDITION dead-end warning e2e ─────────────────


@pytest.mark.asyncio
async def test_condition_deadend_emits_warning(caplog):
    """A CONDITION that selects an unwired branch dead-ends (no successor).
    The behavior is unchanged (nothing wrong fires), but it now emits a
    WARNING so the silent stall is observable."""
    workflow = Workflow(
        id="wf-j3", name="cond-deadend", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="cond", node_type=NodeType.CONDITION,
                         config={"expression": "1 == 2"}),  # false
            WorkflowNode(id="wired_true", node_type=NodeType.COMPUTE, config={"expression": "42"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="cond"),
            # Only the true branch is wired; the chosen false branch dead-ends.
            WorkflowEdge(id="e_t", source_node_id="cond", target_node_id="wired_true",
                         source_port_id="true_branch"),
        ],
    )
    harness = _build_harness(workflow)
    with caplog.at_level(logging.WARNING):
        run = await _start_and_drive(harness, workflow, "run-j3")

    assert run.status == RunStatus.COMPLETED
    completed = {e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)}
    assert "wired_true" not in completed          # wrong branch still never fires
    assert "no outgoing edge accepted" in caplog.text   # ...but the dead-end is now logged


# ─────────────────────── J5 — SWITCH type-aware match e2e ────────────────────


@pytest.mark.asyncio
async def test_switch_legacy_int_matches_float_case_e2e():
    """A SWITCH legacy ``cases`` value ``1.0`` matches an int input ``1``
    end-to-end (pre-fix: ``str(1) != str(1.0)`` fell to no-match)."""
    workflow = Workflow(
        id="wf-j5", name="switch-type-aware", version="1",
        nodes=[
            WorkflowNode(id="trigger", node_type=NodeType.TRIGGER),
            WorkflowNode(id="sw", node_type=NodeType.SWITCH,
                         config={"expression": "{{$input.n}}",
                                 "cases": [{"value": 1.0, "target": "matched"}]}),
            WorkflowNode(id="matched", node_type=NodeType.COMPUTE, config={"expression": "99"}),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="trigger", target_node_id="sw"),
            WorkflowEdge(id="e_m", source_node_id="sw", target_node_id="matched"),
        ],
    )
    harness = _build_harness(workflow)
    run = await _start_and_drive(harness, workflow, "run-j5", variables={"input": {"n": 1}})

    assert run.status == RunStatus.COMPLETED
    completed = {e.node_id for e in harness["publisher"].events_of(EventTopic.NODE_COMPLETED)}
    assert "matched" in completed, "int 1 must match float-1.0 case type-aware"
