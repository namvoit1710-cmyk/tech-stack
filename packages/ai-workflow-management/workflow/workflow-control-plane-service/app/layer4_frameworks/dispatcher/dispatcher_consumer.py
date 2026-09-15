"""Phase 2b — DispatcherConsumer and its publisher tee.

The DispatcherConsumer computes downstream fan-out from
``NodeOutputProduced`` events using the same pure ``compute_fanout``
that powers the engine's inline ``_route_to_successors``. In shadow
mode (``DISPATCHER_MODE=shadow``) both the inline path and the
consumer produce their own ``EdgeTraversed`` / ``NodeInput`` /
``WireSkipped`` event streams; a ``DispatcherParityComparator`` then
asserts the two streams agree.

Why a publisher tee instead of a broker subscription:
    For Phase 2b we need the shadow consumer to see every
    ``NodeOutputProduced`` emission deterministically, including in
    mock/console mode where no broker exists. Wrapping the publisher
    chain with ``DispatcherTeeingPublisher`` gives in-process
    observation with zero coupling to the transport, and the tee
    preserves event ordering per run (publisher calls are
    synchronous).

Once Phase 3a flips the authoritative bit and the inline path is
deleted, the consumer migrates to a broker subscription and the tee
is removed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.layer1_domain.events import (
    EdgeTraversed,
    EventTopic,
    LoopBodyDispatched,
    LoopExitDispatched,
    NodeInput,
    NodeOutputProduced,
    WireDeadLetter,
    WireSkipped,
)
from app.layer1_domain.fanout import FanoutDecision
from app.layer1_domain.value_objects.event_id import EventId
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.task_status import TaskStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DispatcherEmission:
    """A single event the DispatcherConsumer would emit in authoritative
    mode. In shadow mode these are captured by the parity comparator
    instead of being published, so inline and dispatcher streams can be
    compared without double-publishing.
    """
    topic: str
    event: Any


class DispatcherConsumer:
    """Compute fan-out from ``NodeOutputProduced`` and record the
    resulting event stream.

    Shadow mode (Phase 2b): emissions are captured in the parity
    comparator, not published. Authoritative mode (Phase 3a): emissions
    publish to the bus and replace the engine's inline routing.
    """

    def __init__(
        self,
        view_store: Any,
        workflow_repo: Any,
        recorder: Any | None = None,
        publisher: Any | None = None,
        mode: str = "shadow",
    ) -> None:
        self._view_store = view_store
        self._workflow_repo = workflow_repo
        self._recorder = recorder
        self._publisher = publisher
        self._mode = mode

    def set_publisher(self, publisher: Any) -> None:
        """Late-bind the publisher.

        Bootstrap needs this because ``DispatcherTeeingPublisher`` wraps
        the event_publisher *after* the consumer is constructed (the
        tee takes the consumer as a dependency). In authoritative mode
        the consumer should publish through the teed publisher so
        dispatcher-emitted events flow through the same event-log /
        realtime decorators as engine emissions — set_publisher is
        called once the tee exists.
        """
        self._publisher = publisher

    def on_node_output_produced(self, event: NodeOutputProduced) -> None:
        """Handle a ``NodeOutputProduced`` event.

        After Phase 3a PR 2 the engine no longer emits edge events
        inline, so the consumer always publishes when a publisher is
        wired. In ``shadow`` mode the emissions are also recorded to
        the parity recorder for diagnostic inspection — the recorder
        is the one-release safety net rather than a drift comparator
        (nothing else emits these events to compare against).
        """
        emissions = self._compute_emissions(event)
        if emissions is None:
            return
        if self._publisher is not None:
            self._publish_emissions(emissions)
        if self._recorder is not None:
            self._recorder.record_dispatcher(event.run_id, emissions)

    def on_loop_body_dispatched(self, event: LoopBodyDispatched) -> None:
        """Phase 3a.3 — emit ``EdgeTraversed`` for each ``LOOP → body``
        edge when the engine enqueues loop-body tasks.

        The engine still owns task creation + enqueue (Phase 3 master-doc
        scope); this hook is emission-only so the Dispatcher remains the
        sole source of ``EdgeTraversed`` for all fan-out paths.
        """
        emissions = self._compute_loop_emissions(
            run_id=event.run_id,
            workflow_id=event.workflow_id,
            loop_node_id=event.loop_node_id,
            target_node_ids=event.body_node_ids,
            causation_id=event.causation_id or event.event_id or event.loop_node_id,
            iteration_index=event.iteration_index,
            root_run_id=event.root_run_id,
            depth=event.depth,
        )
        if emissions is None:
            return
        if self._publisher is not None:
            self._publish_emissions(emissions)
        if self._recorder is not None:
            self._recorder.record_dispatcher(event.run_id, emissions)

    def on_loop_exit_dispatched(self, event: LoopExitDispatched) -> None:
        """Phase 3a.3 — emit ``EdgeTraversed`` for each ``LOOP → exit``
        edge when the engine enqueues exit tasks after the final
        iteration. Same ``iteration_index=None`` by convention because
        exit-path edges are not iteration-scoped.
        """
        emissions = self._compute_loop_emissions(
            run_id=event.run_id,
            workflow_id=event.workflow_id,
            loop_node_id=event.loop_node_id,
            target_node_ids=event.exit_node_ids,
            causation_id=event.causation_id or event.event_id or event.loop_node_id,
            iteration_index=None,
            root_run_id=event.root_run_id,
            depth=event.depth,
        )
        if emissions is None:
            return
        if self._publisher is not None:
            self._publish_emissions(emissions)
        if self._recorder is not None:
            self._recorder.record_dispatcher(event.run_id, emissions)

    def _compute_loop_emissions(
        self,
        *,
        run_id: str,
        workflow_id: str,
        loop_node_id: str,
        target_node_ids: list[str],
        causation_id: str,
        iteration_index: int | None,
        root_run_id: str,
        depth: int,
    ) -> list["DispatcherEmission"] | None:
        """Translate a loop dispatch announcement into one
        ``EdgeTraversed`` emission per (loop_node → target) edge.

        Returns ``None`` when the workflow cannot be resolved so the
        caller silently no-ops — matches the fault-tolerant pattern of
        ``_compute_emissions`` for ``NodeOutputProduced``.
        """
        if not run_id or not loop_node_id or not target_node_ids:
            return []
        if self._workflow_repo is None:
            return None
        try:
            workflow = self._workflow_repo.get_by_id(workflow_id)
        except Exception:
            workflow = None
        if workflow is None:
            return None

        view = self._view_store.get(run_id) if self._view_store is not None else None
        outgoing = (
            view.topology.edge_map.get(loop_node_id, [])
            if view is not None
            else []
        )
        # When we don't have a view (tests, cold start), fall back to
        # deriving edges directly from the workflow definition.
        if not outgoing and workflow is not None:
            outgoing = [
                e for e in (workflow.edges or [])
                if e.source_node_id == loop_node_id
            ]

        target_set = set(target_node_ids)
        emissions: list[DispatcherEmission] = []
        for edge in outgoing:
            if edge.target_node_id not in target_set:
                continue
            edge_event_id = EventId.derive(
                causation_id, f"edge:{edge.id}:{edge.target_node_id}",
            )
            emissions.append(DispatcherEmission(
                topic=EventTopic.EDGE_TRAVERSED,
                event=EdgeTraversed(
                    event_id=edge_event_id,
                    run_id=run_id,
                    workflow_id=workflow_id,
                    edge_id=edge.id,
                    source_node_id=edge.source_node_id,
                    target_node_id=edge.target_node_id,
                    source_port_id=edge.source_port_id or "out_default",
                    root_run_id=root_run_id or run_id,
                    depth=depth,
                    source="dispatcher",
                    iteration_index=iteration_index,
                ),
            ))
        return emissions

    def _publish_emissions(self, emissions: list["DispatcherEmission"]) -> None:
        """Publish each dispatcher emission via the bound publisher.

        Swallows per-event exceptions so one bad publish never poisons
        the rest of the fan-out or the upstream ``NodeOutputProduced``
        publish chain.
        """
        for emission in emissions:
            try:
                self._publisher.publish(emission.topic, emission.event)
            except Exception:
                logger.exception(
                    "DispatcherConsumer failed to publish %s for run=%s",
                    emission.topic,
                    getattr(emission.event, "run_id", "?"),
                )

    # ----- internal -----

    def _compute_emissions(
        self, event: NodeOutputProduced,
    ) -> list[DispatcherEmission] | None:
        """Translate a ``NodeOutputProduced`` into the event stream the
        Dispatcher would emit in authoritative mode.
        """
        if not event.run_id or not event.node_id:
            return None
        # ``__bootstrap__`` events seed initial variables — no fan-out.
        if event.node_id == "__bootstrap__":
            return []

        view = self._view_store.get(event.run_id)
        if view is None:
            logger.debug(
                "DispatcherConsumer: no view for run %s — skipping",
                event.run_id,
            )
            return None

        workflow = None
        if self._workflow_repo is not None:
            try:
                workflow = self._workflow_repo.get_by_id(event.workflow_id)
            except Exception:
                workflow = None
        if workflow is None:
            logger.debug(
                "DispatcherConsumer: no workflow for %s — skipping",
                event.workflow_id,
            )
            return None

        source_node = workflow.get_node(event.node_id)
        if source_node is None:
            return []

        topology = view.topology
        outgoing = topology.edge_map.get(event.node_id, [])

        # Branching node: taken_edge_ports already tells us the outcome.
        if event.taken_edge_ports:
            return self._emit_branching(event, outgoing)

        # Port-routed node: call compute_fanout with the handler's port.
        task_status = (
            TaskStatus.COMPLETED
            if event.output_port != "failure"
            else TaskStatus.FAILED
        )
        port_override = (
            event.output_port
            if event.output_port and event.output_port != "out_default"
            else None
        )
        decision = FanoutDecision.compute(
            causation_id=event.event_id or event.node_id,
            source_node_id=event.node_id,
            source_node_type=source_node.node_type,
            source_task_status=task_status,
            outgoing_edges=outgoing,
            expected_port_override=port_override,
        )
        return self._emit_from_decision(event, decision, outgoing)

    def _emit_branching(
        self,
        event: NodeOutputProduced,
        outgoing: list,
    ) -> list[DispatcherEmission]:
        """Produce edge/node-input events for a CONDITION/SWITCH decision."""
        emissions: list[DispatcherEmission] = []
        from app.layer1_domain.value_objects.event_id import EventId
        taken_ports = event.taken_edge_ports
        for edge in outgoing:
            if edge.id not in taken_ports:
                continue
            port_id = taken_ports[edge.id]
            target_id = edge.target_node_id
            edge_event_id = EventId.derive(
                event.event_id or event.node_id,
                f"edge:{edge.id}:{target_id}",
            )
            emissions.append(DispatcherEmission(
                topic=EventTopic.EDGE_TRAVERSED,
                event=EdgeTraversed(
                    event_id=edge_event_id,
                    run_id=event.run_id,
                    workflow_id=event.workflow_id,
                    edge_id=edge.id,
                    source_node_id=edge.source_node_id,
                    target_node_id=target_id,
                    source_port_id=port_id,
                    root_run_id=event.root_run_id or event.run_id,
                    depth=event.depth,
                    source="dispatcher",
                ),
            ))
            emissions.append(DispatcherEmission(
                topic=EventTopic.NODE_INPUT,
                event=NodeInput(
                    event_id=EventId.derive(
                        event.event_id or event.node_id,
                        f"input:{target_id}",
                    ),
                    run_id=event.run_id,
                    workflow_id=event.workflow_id,
                    node_id=target_id,
                    root_run_id=event.root_run_id or event.run_id,
                    depth=event.depth,
                    source="dispatcher",
                ),
            ))
        return emissions

    def _emit_from_decision(
        self,
        event: NodeOutputProduced,
        decision: FanoutDecision,
        outgoing: list,
    ) -> list[DispatcherEmission]:
        """Produce edge/node-input/wire-skipped/dead-letter events from
        a ``compute_fanout`` decision.
        """
        from app.layer1_domain.value_objects.event_id import EventId
        emissions: list[DispatcherEmission] = []

        # Edge + NodeInput per taken action.
        for action in decision.actions:
            emissions.append(DispatcherEmission(
                topic=EventTopic.EDGE_TRAVERSED,
                event=EdgeTraversed(
                    event_id=action.event_id,
                    run_id=event.run_id,
                    workflow_id=event.workflow_id,
                    edge_id=action.edge_id,
                    source_node_id=action.source_node_id,
                    target_node_id=action.target_node_id,
                    source_port_id=action.source_port_id,
                    root_run_id=event.root_run_id or event.run_id,
                    depth=event.depth,
                    source="dispatcher",
                ),
            ))
            emissions.append(DispatcherEmission(
                topic=EventTopic.NODE_INPUT,
                event=NodeInput(
                    event_id=EventId.derive(
                        event.event_id or event.node_id,
                        f"input:{action.target_node_id}",
                    ),
                    run_id=event.run_id,
                    workflow_id=event.workflow_id,
                    node_id=action.target_node_id,
                    root_run_id=event.root_run_id or event.run_id,
                    depth=event.depth,
                    source="dispatcher",
                ),
            ))

        # WireSkipped — port-filtered drops.
        if decision.expected_port:
            taken_edge_ids = {a.edge_id for a in decision.actions}
            for edge in outgoing:
                if not edge.source_port_id:
                    continue
                if edge.source_port_id == decision.expected_port:
                    continue
                if edge.id in taken_edge_ids:
                    continue
                emissions.append(DispatcherEmission(
                    topic=EventTopic.WIRE_SKIPPED,
                    event=WireSkipped(
                        event_id=EventId.derive(
                            event.event_id or event.node_id,
                            f"skip:{edge.id}",
                        ),
                        run_id=event.run_id,
                        workflow_id=event.workflow_id,
                        edge_id=edge.id,
                        source_node_id=edge.source_node_id,
                        target_node_id=edge.target_node_id,
                        source_port_id=edge.source_port_id,
                        reason="port_filter",
                        root_run_id=event.root_run_id or event.run_id,
                        depth=event.depth,
                        source="dispatcher",
                    ),
                ))

        # WireDeadLetter — node produced output but nothing was taken.
        if outgoing and not decision.actions and not decision.deferred_target_ids:
            dropped_ids = [e.id for e in outgoing]
            emissions.append(DispatcherEmission(
                topic=EventTopic.WIRE_DEAD_LETTER,
                event=WireDeadLetter(
                    event_id=EventId.derive(
                        event.event_id or event.node_id,
                        f"dead_letter:{event.node_id}",
                    ),
                    run_id=event.run_id,
                    workflow_id=event.workflow_id,
                    source_node_id=event.node_id,
                    source_port_id=event.output_port,
                    dropped_edge_ids=dropped_ids,
                    root_run_id=event.root_run_id or event.run_id,
                    depth=event.depth,
                    source="dispatcher",
                ),
            ))

        return emissions


class DispatcherTeeingPublisher:
    """Publisher decorator that also feeds ``NodeOutputProduced`` events
    to the ``DispatcherConsumer``.

    Wraps an inner publisher so every ``publish``/``publish_many`` call
    goes through normally, and on ``NodeOutputProduced`` additionally
    fires ``dispatcher.on_node_output_produced(event)``. Used only when
    ``DISPATCHER_MODE`` is ``shadow``.
    """

    def __init__(self, inner: Any, dispatcher: DispatcherConsumer) -> None:
        self._inner = inner
        self._dispatcher = dispatcher

    def publish(self, topic: str, event: Any) -> None:
        self._inner.publish(topic, event)
        self._feed(topic, event)

    def publish_many(self, events: list) -> None:
        self._inner.publish_many(events)
        for entry in events:
            # Support both (topic, event) tuples and event-only iterables.
            if isinstance(entry, tuple) and len(entry) == 2:
                self._feed(entry[0], entry[1])

    def _feed(self, topic: str, event: Any) -> None:
        try:
            if topic == EventTopic.NODE_OUTPUT_PRODUCED and isinstance(event, NodeOutputProduced):
                self._dispatcher.on_node_output_produced(event)
            elif topic == EventTopic.LOOP_BODY_DISPATCHED and isinstance(event, LoopBodyDispatched):
                self._dispatcher.on_loop_body_dispatched(event)
            elif topic == EventTopic.LOOP_EXIT_DISPATCHED and isinstance(event, LoopExitDispatched):
                self._dispatcher.on_loop_exit_dispatched(event)
        except Exception:
            logger.exception(
                "DispatcherConsumer threw on event %s — swallowed to "
                "protect the publish chain",
                getattr(event, "event_id", "?"),
            )

    def __getattr__(self, name: str) -> Any:
        # Forward any other publisher methods to the inner publisher
        # (e.g. drain, flush, set_realtime_consumer).
        return getattr(self._inner, name)
