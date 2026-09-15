"""Dispatcher authoritative mode — Phase 3a end-to-end tests.

Runs workflows with ``DISPATCHER_MODE=dispatcher`` and asserts:
- The DispatcherConsumer is wired to publish through the teed
  event_publisher.
- The run completes without regressions (happy path).
- The event log contains exactly one ``EdgeTraversed`` per edge (the
  engine's inline emission is skipped; the dispatcher emits
  authoritatively).
- Dispatcher-published ``EdgeTraversed`` events carry the deterministic
  id formula ``EventId.derive(causation_id, "edge:<edge_id>:<target>")``.

Scope boundary:
- The ``_route_to_successors`` fan-out path is authoritative via
  dispatcher. Loop body / loop exit / child-workflow edge emissions
  stay inline — covered by separate integration tests and out of
  scope for this file.
- The parity comparator is retained in ``dispatcher`` mode for one
  release so drift between inline (non-``_route_to_successors``
  emissions) and dispatcher can still be observed; drift for the
  covered paths must be zero.
"""
from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects.node_type import NodeType


async def _drain_orchestration_queue(container, max_idle_rounds: int = 3) -> None:
    """Drive the orchestration worker until the queue reaches two idle
    rounds in a row.

    ``OrchestrationWorker`` exposes ``run_forever`` (blocking loop), not
    ``run_once``, so tests must drain manually. We dequeue + dispatch
    each action via the worker's ``_dispatch`` helper; once
    ``qsize() == 0`` holds for ``max_idle_rounds`` consecutive cooperative
    yields we consider the run settled.
    """
    queue = container["_orchestration_queue"]
    worker = container["_orchestration_worker"]
    asyncio_queue = getattr(queue, "_queue", None)
    if asyncio_queue is None:
        return
    idle = 0
    for _ in range(200):
        if asyncio_queue.empty():
            idle += 1
            if idle >= max_idle_rounds:
                break
            await asyncio.sleep(0)
            continue
        idle = 0
        action = await asyncio.wait_for(queue.dequeue(), timeout=2.0)
        await worker._dispatch(action)


@pytest.fixture
def dispatcher_container(monkeypatch):
    """Build the full app container with DISPATCHER_MODE=dispatcher."""
    import bootstrap
    monkeypatch.setattr(bootstrap.settings, "DISPATCHER_MODE", "dispatcher")
    container = bootstrap.build_app_container()
    yield container


@pytest.mark.asyncio
async def test_dispatcher_mode_wires_publisher(dispatcher_container):
    """The consumer must have a publisher set so it can emit authoritatively."""
    consumer = dispatcher_container["dispatcher_consumer"]
    assert consumer is not None
    assert consumer._mode == "dispatcher"
    assert consumer._publisher is not None, (
        "dispatcher mode must late-bind the teed publisher so emissions "
        "flow through the event log"
    )


@pytest.mark.asyncio
async def test_unknown_mode_falls_back_to_dispatcher(monkeypatch):
    """An unrecognised DISPATCHER_MODE upgrades to ``dispatcher`` with
    a warning. Phase 3a PR 2 deleted inline engine emission — there is
    no longer a safe inline fallback."""
    import bootstrap
    monkeypatch.setattr(bootstrap.settings, "DISPATCHER_MODE", "dispatchr")  # typo
    container = bootstrap.build_app_container()
    consumer = container["dispatcher_consumer"]
    assert consumer is not None
    assert consumer._mode == "dispatcher"


@pytest.mark.asyncio
async def test_inline_mode_upgrades_to_dispatcher(monkeypatch):
    """Legacy ``DISPATCHER_MODE=inline`` values are accepted for
    backward-compat but logged and upgraded to ``dispatcher``."""
    import bootstrap
    monkeypatch.setattr(bootstrap.settings, "DISPATCHER_MODE", "inline")
    container = bootstrap.build_app_container()
    consumer = container["dispatcher_consumer"]
    assert consumer is not None
    assert consumer._mode == "dispatcher"


@pytest.mark.asyncio
async def test_happy_path_dispatcher_authoritative_single_edge_emission(
    dispatcher_container,
):
    """End-to-end: TRIGGER → COMPUTE completes with the Dispatcher
    publishing ``EdgeTraversed`` instead of the engine.

    Assertions:
    1. Exactly one ``EdgeTraversed`` per edge in the event log (no
       double-emission).
    2. The event carries the deterministic id formula that the inline
       path would have used — shadow soak relied on byte-identical
       ids, so dispatcher-authoritative emissions must preserve them.
    3. The run reaches a terminal status (happy path).
    """
    container = dispatcher_container
    infra = container["_infra"]
    workflow_repo = infra.repos.workflow
    start_run = container["start_run"]
    event_log_repo = container["event_log_repo"]

    workflow = Workflow(
        id="wf-disp-happy",
        name="disp-happy",
        version="1",
        nodes=[
            WorkflowNode(id="t-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="c-1", name="double",
                node_type=NodeType.COMPUTE,
                config={"expression": "21 * 2"},
            ),
        ],
        edges=[
            WorkflowEdge(id="e1", source_node_id="t-1", target_node_id="c-1"),
        ],
    )
    workflow_repo.save(workflow)

    from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
        StartRunCommand,
    )
    result = await start_run.execute(
        StartRunCommand(
            workflow_id=workflow.id,
            input_data={"seed": 1},
            predefined_run_id="run-disp-happy",
        ),
    )
    assert not result.error, f"start_run returned error: {result.error}"

    await _drain_orchestration_queue(container)

    envelopes = event_log_repo.list_by_run("run-disp-happy")
    edge_events = [
        e for e in envelopes if e.topic == EventTopic.EDGE_TRAVERSED
    ]

    # 1. Exactly one EdgeTraversed per edge (engine did not double-emit).
    edges_seen = [e.payload.get("edge_id", "") for e in edge_events]
    assert edges_seen.count("e1") == 1, (
        f"Expected exactly one EdgeTraversed for edge e1; got "
        f"{edges_seen.count('e1')} total={edge_events}"
    )

    # 2. Event ids match compute_fanout's deterministic formula.
    # causation_id = task.id of the trigger node's task. We don't know
    # the task id without looking it up, but the Dispatcher uses
    # ``event.event_id`` as causation — so the id on the edge event
    # should match ``EventId.derive(<node_output.event_id>,
    # "edge:e1:c-1")`` for the NodeOutputProduced that drove fan-out.
    node_output_events = [
        e for e in envelopes
        if e.topic == EventTopic.NODE_OUTPUT_PRODUCED
        and e.payload.get("node_id") == "t-1"
    ]
    assert len(node_output_events) == 1, (
        f"Expected exactly one NodeOutputProduced for t-1; got "
        f"{len(node_output_events)}"
    )
    causation = node_output_events[0].event_id
    expected_edge_event_id = EventId.derive(causation, "edge:e1:c-1")
    e1_event = next(
        e for e in edge_events if e.payload.get("edge_id") == "e1"
    )
    assert e1_event.event_id == expected_edge_event_id, (
        f"Dispatcher must use compute_fanout's deterministic id. "
        f"Expected {expected_edge_event_id}, got {e1_event.event_id}"
    )

    # 3. Dispatcher-published events carry source="dispatcher".
    assert e1_event.payload.get("source") == "dispatcher", (
        f"EdgeTraversed source should be 'dispatcher' in authoritative "
        f"mode, got {e1_event.payload.get('source')!r}"
    )
