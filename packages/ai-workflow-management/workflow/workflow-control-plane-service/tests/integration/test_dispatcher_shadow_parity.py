"""Dispatcher shadow parity harness — Phase 2b engine split.

Runs representative workflow shapes with ``DISPATCHER_MODE=shadow``
wired end-to-end via ``bootstrap.build_app_container``. Asserts zero
drift at the ``DispatcherParityRecorder`` after each run finalizes.

These tests are the CI gate for dispatcher-shadow rollout: any drift
is a real defect (emission gap, DispatcherConsumer bug, or
deterministic-id mismatch). Pre-existing baseline test failures in the
repo are unrelated to this path — the engine, dispatcher, and
comparator are exercised only via their public API.

Scenarios covered today:
- Harness wiring: shadow mode instantiates DispatcherConsumer + recorder.
- Happy path: TRIGGER → COMPUTE produces identical inline / dispatcher
  emission streams for every ``EdgeTraversed`` / ``NodeInput`` /
  ``WireSkipped`` / ``WireDeadLetter`` by ``event_id``.

Deferred scenarios (see ``docs/design/phase-2-dispatcher-extraction.md``):
- Cancel cascade, HITL resume: pending fail-path ``NodeOutputProduced``
  emission for the on_failure=skip WORKFLOW node at
  ``orchestration_engine.py:2715-2723``.
- Child-workflow happy path: covered at the unit level; end-to-end
  integration to be added once the parent-completion emission lands
  (already fixed by Phase 2b Gap 1).
"""
from __future__ import annotations

import asyncio

import pytest

from app.layer1_domain.entities.workflow import Workflow, WorkflowEdge, WorkflowNode
from app.layer1_domain.events import (
    EventTopic,
    NodeOutputProduced,
)
from app.layer1_domain.value_objects.node_type import NodeType


@pytest.fixture
def shadow_dispatcher_container(monkeypatch):
    """Build the full app container with DISPATCHER_MODE=shadow.

    ``PROJECTOR_READ_MODE`` stays at its default (``projector``) so the
    dispatcher reads the same view-store the engine now reads after
    Phase 2a.
    """
    import bootstrap
    monkeypatch.setattr(bootstrap.settings, "DISPATCHER_MODE", "shadow")
    container = bootstrap.build_app_container()
    yield container


@pytest.mark.asyncio
async def test_shadow_mode_wires_dispatcher_consumer(shadow_dispatcher_container):
    """Sanity: shadow mode wires both the consumer and the comparator."""
    from app.layer4_frameworks.dispatcher import (
        DispatcherConsumer,
        DispatcherParityComparator,
    )
    container = shadow_dispatcher_container
    assert container["dispatcher_consumer"] is not None
    assert isinstance(container["dispatcher_consumer"], DispatcherConsumer)
    assert container["dispatcher_parity_comparator"] is not None
    assert isinstance(
        container["dispatcher_parity_comparator"], DispatcherParityComparator,
    )


@pytest.mark.asyncio
async def test_shadow_mode_wires_parity_comparator(monkeypatch):
    """``DISPATCHER_MODE=shadow`` wires the parity comparator on top of
    the authoritative consumer so operators can inspect dispatcher
    emissions via the recorder for one release. ``dispatcher`` mode
    leaves the comparator unwired."""
    import bootstrap
    monkeypatch.setattr(bootstrap.settings, "DISPATCHER_MODE", "shadow")
    shadow = bootstrap.build_app_container()
    assert shadow["dispatcher_parity_comparator"] is not None

    monkeypatch.setattr(bootstrap.settings, "DISPATCHER_MODE", "dispatcher")
    dispatcher = bootstrap.build_app_container()
    assert dispatcher["dispatcher_parity_comparator"] is None


@pytest.mark.asyncio
async def test_happy_path_workflow_shadow_dispatcher_zero_drift(
    shadow_dispatcher_container,
):
    """End-to-end: a TRIGGER→COMPUTE run emits matching inline/dispatcher
    event streams — zero drift.

    The ``DispatcherTeeingPublisher`` feeds every ``NodeOutputProduced``
    emission to the consumer. For each event the consumer computes
    ``compute_fanout`` and records the resulting ``EdgeTraversed`` /
    ``NodeInput`` events. The engine's inline ``_route_to_successors``
    publishes the same ``EdgeTraversed`` events with the same
    deterministic ids. Finalizing the run in the comparator must find
    identical ``(topic, event_id)`` sets on both sides.
    """
    container = shadow_dispatcher_container
    infra = container["_infra"]
    workflow_repo = infra.repos.workflow
    start_run = container["start_run"]
    comparator = container["dispatcher_parity_comparator"]
    event_log_repo = container["event_log_repo"]

    workflow = Workflow(
        id="wf-dispatch-happy",
        name="dispatch-happy",
        version="1",
        nodes=[
            WorkflowNode(id="t-1", name="trigger", node_type=NodeType.TRIGGER, config={}),
            WorkflowNode(
                id="c-1", name="double",
                node_type=NodeType.COMPUTE,
                config={"expression": "21 * 2"},
            ),
        ],
        edges=[WorkflowEdge(id="e1", source_node_id="t-1", target_node_id="c-1")],
    )
    workflow_repo.save(workflow)

    from app.layer2_application.features.start_run.use_cases.start_run_use_case import (
        StartRunCommand,
    )
    result = await start_run.execute(
        StartRunCommand(
            workflow_id=workflow.id,
            input_data={"seed": 1},
            predefined_run_id="run-dispatch-happy",
        ),
    )
    assert not result.error, f"start_run returned error: {result.error}"

    # Drain the orchestration queue so the engine finishes the run.
    worker = container["_orchestration_worker"]
    for _ in range(20):
        try:
            await asyncio.wait_for(worker.run_once(), timeout=2.0)
        except (asyncio.TimeoutError, AttributeError):
            break

    # Feed the inline side of the comparator from the event log. The
    # ``EventLogAppendingPublisher`` captures every publish, so this is
    # equivalent to a broker subscriber seeing each emission. The
    # DispatcherConsumer has already been fed via the publisher tee on
    # ``NodeOutputProduced`` emissions.
    _COMPARED_TOPICS = {
        EventTopic.EDGE_TRAVERSED,
        EventTopic.NODE_INPUT,
        EventTopic.WIRE_SKIPPED,
        EventTopic.WIRE_DEAD_LETTER,
    }
    envelopes = event_log_repo.list_by_run("run-dispatch-happy")
    # Phase 4 Sub 4.2 added ``RunStartConsumer`` which emits
    # entry-node ``NodeInput`` envelopes when ``EXECUTOR_MODE !=
    # in_process``. The dispatcher will never emit these (entry
    # nodes have no upstream ``NodeOutputProduced`` to fan out from)
    # so they are NOT part of the dispatcher-vs-engine parity
    # comparison. Filter them out by their source tag.
    _NON_DISPATCHER_SOURCES = {"run-start-consumer", "task-creator"}
    for envelope in envelopes:
        if envelope.topic not in _COMPARED_TOPICS:
            continue
        payload = dict(envelope.payload or {})
        if payload.get("source") in _NON_DISPATCHER_SOURCES:
            continue
        # Reconstruct a minimal event shape the comparator can key on —
        # only the fields _event_payload() reads. Pop ``event_id`` from
        # the payload before splatting since the publisher chain
        # already mirrors it onto every envelope's payload via
        # ``dataclasses.asdict``; without the pop, ``**payload`` and
        # ``event_id=envelope.event_id`` collide on the kwarg.
        payload.pop("event_id", None)
        event_ns = _PayloadNamespace(event_id=envelope.event_id, **payload)
        comparator.record_inline("run-dispatch-happy", envelope.topic, event_ns)

    # Finalize the run — compute drift.
    drifts = comparator.finalize_run("run-dispatch-happy")

    # The happy path must produce byte-identical emission streams.
    assert drifts == [], (
        f"dispatcher shadow parity drift observed on happy path: {drifts}"
    )
    assert comparator.recorder.total == 0


# ---- helpers ----


class _PayloadNamespace:
    """Minimal attribute-access wrapper for an event-log payload so the
    comparator's ``getattr(event, <field>, "")`` lookups work.
    """

    def __init__(self, **kwargs) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)
