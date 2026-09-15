"""End-to-end smoke test for ``EXECUTOR_MODE=executor``.

Boots the full app container with ``EXECUTOR_MODE=executor`` and runs a
representative TRIGGER→COMPUTE workflow through the *event-driven*
executor path, asserting it completes correctly.

## What "executor mode" means here

Under ``EXECUTOR_MODE=executor`` the ``OrchestrationEngine`` STOPS
invoking node handlers in-process. At each handler site it publishes a
``NodeInput`` event and returns (``orchestration_engine.py``). The chain
that drives a run forward is then:

    RunStatusChanged(started) → RunStartConsumer
      → NodeInput(entry) → TaskCreatorConsumer (creates Task)
                         → ExecutorConsumer (invokes handler)
        → NodeOutputProduced → DispatcherConsumer (fan-out)
            → NodeInput(successor) → TaskCreatorConsumer + ExecutorConsumer (cycle)
        → NodeCompleted → RunCompletionConsumer
            → CheckRunCompletionAction → OrchestrationWorker
                → orchestrate_check_completion → run reaches COMPLETED

The ExecutorConsumer also transitions each node's Task entity to a
terminal status so ``check_run_completion`` (which decides quiescence
purely from task status) sees the run finish.

## Why the harness only feeds the ExecutorConsumer

In production the consumers are broker subscribers and the broker is the
transport. Mock mode has no broker, but every consumer EXCEPT the
ExecutorConsumer is a synchronous publisher-tee, so RunStart, TaskCreator,
Dispatcher and RunCompletion all fire in-process as events are published.
The ExecutorConsumer is the one async consumer (it invokes handlers off
its own queue), so the harness plays only the broker's *transport* role
for it: it reads each ``NodeInput`` from the event log and hands it to
``ExecutorConsumer.notify`` (its documented memory-mode entry point),
then drains. Task creation is NOT hand-fed — it flows through the real
RunStart/Dispatcher → TaskCreator tee chain, which is what
``test_executor_mode_invokes_handlers_and_fans_out`` asserts.

## Scope

This verifies the COMBINED-pod topology (one process; the executor
publishes through the in-process tee chain). The completion trigger is
race-safe there because a node's ``NodeOutputProduced`` synchronously
creates its successor's Task before its ``NodeCompleted`` enqueues the
completion check. A true process split (executor as a separate pod /
broker subscribers) breaks that happens-before and is out of scope —
see ``RunCompletionConsumer`` and ``orchestrate_check_completion``.
"""
from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
    StartRunCommand,
)

_TERMINAL = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


@pytest.fixture
def executor_container(monkeypatch):
    """Full app container built with ``EXECUTOR_MODE=executor``.

    ``DISPATCHER_MODE`` stays at its default (``dispatcher`` — the
    authoritative fan-out path) so the chain matches a real executor-mode
    deployment.
    """
    import bootstrap
    monkeypatch.setattr(bootstrap.settings, "EXECUTOR_MODE", "executor")
    return bootstrap.build_app_container()


def _trigger_compute_workflow() -> Workflow:
    return Workflow(
        id="wf-executor-e2e",
        name="executor-e2e",
        version="1",
        nodes=[
            WorkflowNode(id="t1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="c1", name="double", node_type=NodeType.COMPUTE,
                config={"expression": "21 * 2"},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="t1", target_node_id="c1")],
    )


async def _drain_worker(worker, max_actions: int = 50) -> None:
    """Dequeue and dispatch every queued orchestration action, returning
    once the queue is empty (dequeue times out).

    Mirrors ``OrchestrationWorker.run_forever`` one action at a time so
    the test stays deterministic instead of racing a background task.
    Drives the worker's real ``_dispatch`` (StartRunAction →
    orchestrate_start, CheckRunCompletionAction →
    orchestrate_check_completion) against the shared queue.
    """
    queue = worker._queue
    for _ in range(max_actions):
        try:
            action = await asyncio.wait_for(queue.dequeue(), timeout=0.2)
        except asyncio.TimeoutError:
            return
        await worker._dispatch(action)


async def _drive_executor_run(container, run_id: str):
    """Run a TRIGGER→COMPUTE workflow through the executor-mode event
    chain and return ``(run, event_log_envelopes, consumers)``.

    Each round: drain the orchestration queue (StartRunAction →
    orchestrate_start; CheckRunCompletionAction → completion checks),
    then deliver any new ``NodeInput`` to the ExecutorConsumer and drain
    it. The executor's NodeOutputProduced fan-out (→ TaskCreator) and its
    NodeCompleted (→ completion-check enqueue) fire synchronously through
    the in-process tee chain during ``drain_once``.
    """
    infra = container["_infra"]
    workflow_repo = infra.repos.workflow
    run_repo = infra.repos.run
    start_run = container["start_run"]
    worker = container["_orchestration_worker"]
    event_log = container["event_log_repo"]
    executor = container["_executor_consumer"]

    workflow_repo.save(_trigger_compute_workflow())

    # ExecutorConsumer.start() captures the running loop so notify() can
    # enqueue onto it (memory mode → no broker subscription).
    executor.start()

    result = await start_run.execute(
        StartRunCommand(
            workflow_id="wf-executor-e2e",
            input_data={"seed": 1},
            predefined_run_id=run_id,
        ),
    )
    assert not result.error, f"start_run returned error: {result.error}"

    seen_inputs: set[str] = set()
    quiet_rounds = 0
    for _ in range(25):
        await _drain_worker(worker)

        new = 0
        for env in event_log.list_by_run(run_id):
            if env.topic != EventTopic.NODE_INPUT or env.event_id in seen_inputs:
                continue
            seen_inputs.add(env.event_id)
            new += 1
            # Only the executor is hand-fed (broker transport sim). Task
            # creation flows through the real tee chain — not the harness.
            executor.notify(dict(env.payload or {}))
        await executor.drain_once()

        run = run_repo.get_by_id(run_id)
        if getattr(run, "status", None) in _TERMINAL:
            await _drain_worker(worker)  # flush any trailing completion check
            break
        quiet_rounds = quiet_rounds + 1 if new == 0 else 0
        if quiet_rounds >= 2:
            break

    return run_repo.get_by_id(run_id), event_log.list_by_run(run_id), {
        "executor": executor,
        "task_creator": container["_task_creator_consumer"],
        "run_start": container["_run_start_consumer"],
        "run_completion": container["_run_completion_consumer"],
    }


def _node_ids_for(envelopes, topic) -> set[str]:
    return {
        (e.payload or {}).get("node_id")
        for e in envelopes
        if e.topic == topic
    }


def _run_status_events(envelopes) -> list[str]:
    return [
        (e.payload or {}).get("status")
        for e in envelopes
        if e.topic == EventTopic.RUN_STATUS_CHANGED
    ]


@pytest.mark.asyncio
async def test_executor_mode_wires_event_driven_consumers(executor_container):
    """Sanity: ``executor`` mode wires the executor + the Phase-4
    publisher-tee consumers (including the run-completion consumer)."""
    executor = executor_container["_executor_consumer"]
    assert executor is not None
    assert executor.mode == "executor"
    assert executor.is_active is True
    assert executor_container["_task_creator_consumer"] is not None
    assert executor_container["_run_start_consumer"] is not None
    assert executor_container["_run_completion_consumer"] is not None


@pytest.mark.asyncio
async def test_executor_mode_invokes_handlers_and_fans_out(executor_container):
    """The executor (not the engine) invokes every handler off the event
    chain, emits the authoritative node events, the dispatcher fans out,
    and the downstream task entity is created through the real tee chain.
    """
    run, envelopes, consumers = await _drive_executor_run(
        executor_container, "run-executor-e2e",
    )

    # The standalone executor — not the engine — ran both node handlers.
    assert consumers["executor"].invocations == 2, (
        "expected the ExecutorConsumer to invoke both node handlers "
        f"(TRIGGER + COMPUTE); got {consumers['executor'].invocations}"
    )

    # Both nodes produced authoritative output + completion events, and
    # both real-node outputs are executor-sourced — i.e. the engine
    # bowed out of handler invocation entirely. (The run also emits one
    # synthetic ``__bootstrap__`` NodeOutputProduced from the control
    # plane to seed the run input; that is not a handler invocation.)
    assert {"t1", "c1"} <= _node_ids_for(envelopes, EventTopic.NODE_COMPLETED)
    executor_outputs = {
        e.payload.get("node_id")
        for e in envelopes
        if e.topic == EventTopic.NODE_OUTPUT_PRODUCED
        and e.payload.get("source") == "wio-executor"
    }
    assert {"t1", "c1"} <= executor_outputs, (
        "expected both workflow nodes' NodeOutputProduced to be "
        f"executor-sourced; got {executor_outputs}"
    )

    # The dispatcher fanned out across the t1→c1 edge.
    assert any(e.topic == EventTopic.EDGE_TRAVERSED for e in envelopes), (
        "expected DispatcherConsumer to emit EdgeTraversed for t1→c1"
    )

    # Downstream Task entity is created via the RunStart/Dispatcher →
    # TaskCreator tee chain (NOT a harness feed). Two tasks: entry t1 +
    # dispatcher-routed successor c1. Proves the line-570 publisher fix —
    # without it the dispatcher's NodeInput bypasses the TaskCreator tee
    # and c1 is never tasked (created would be 1).
    assert consumers["task_creator"].created_count == 2, (
        "expected TaskCreator to create both the entry and the "
        f"dispatcher-routed task; got {consumers['task_creator'].created_count}"
    )
    assert consumers["run_start"].dispatched_count == 1


@pytest.mark.asyncio
async def test_executor_mode_run_reaches_completion(executor_container):
    """The run reaches COMPLETED purely through the event chain, exactly
    once, and only after both nodes have completed.

    Guards the failure mode the completion gap created: the run flapping
    to COMPLETED on an intermediate node before its successor ran (which
    is irreversible — ``check_run_completion`` returns early for terminal
    runs). Asserting "exactly one completed event" catches a premature
    completion as a duplicate / out-of-order status.
    """
    run, envelopes, consumers = await _drive_executor_run(
        executor_container, "run-executor-e2e-complete",
    )

    statuses = _run_status_events(envelopes)
    assert statuses.count("completed") == 1, (
        f"expected exactly one run.status=completed event; got {statuses}"
    )
    assert statuses[-1] == "completed", (
        f"run did not finish in the completed state; statuses={statuses}"
    )
    assert run.status == RunStatus.COMPLETED

    # Completion came after BOTH nodes finished — not a premature flap.
    assert {"t1", "c1"} <= _node_ids_for(envelopes, EventTopic.NODE_COMPLETED)
    # The completion consumer drove it (one check enqueued per terminal node).
    assert consumers["run_completion"].enqueued_count >= 1
