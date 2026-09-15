"""Phase 4 Sub 4.2 — RunStartConsumer.

Closes Gate 3 Gaps 2 and 3 ([`phase-3-verification.md`](docs/design/phase-3-verification.md)
§3): under ``EXECUTOR_MODE=executor`` ``orchestrate_start`` and
``_start_child_workflow`` no longer drive entry-node dispatch
inline — they emit ``RunStatusChanged`` / ``ChildRunStarted`` and return.
This consumer subscribes to those events and emits ``NodeInput``
for each entry node, completing the chain:

    RunStatusChanged              → RunStartConsumer
        → NodeInput (per entry node)
            → TaskCreatorConsumer (Sub 4.1)  → creates Task + emits NodeDispatched
            → ExecutorConsumer (Sub 3.5)     → invokes handler + emits NodeOutputProduced
                → DispatcherConsumer (Phase 3a) → fan-out, emits NodeInput per successor
                    → TaskCreatorConsumer + ExecutorConsumer (cycle)

For replay safety the emitted ``NodeInput`` events use deterministic
event ids derived from ``(source_event_id, "entry:" + node_id)`` so
the event log dedupes redeliveries of the entry-node dispatch.

Mode gating: bootstrap wires the consumer only when
``EXECUTOR_MODE != "in_process"``. Under default ``in_process``,
``orchestrate_start`` continues to walk entry nodes inline — running
both paths would double-emit ``NodeInput`` for entries.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.events import (
    ChildRunStarted,
    EventTopic,
    NodeInput,
    RunStatusChanged,
)
from app.layer1_domain.value_objects.event_id import EventId


_logger = logging.getLogger(__name__)


class RunStartConsumer:
    """Publisher-tee on ``RunStatusChanged`` + ``ChildRunStarted``; emits one
    ``NodeInput`` per entry node so the rest of the event chain
    drives the run forward.

    Like the other publisher-tee consumers, this one never raises.
    Failures are logged + swallowed.
    """

    def __init__(
        self,
        *,
        run_repo: Any,
        workflow_repo: Any,
        publisher: Any | None = None,
    ) -> None:
        self._run_repo = run_repo
        self._workflow_repo = workflow_repo
        self._publisher = publisher
        self._dispatched_count: int = 0
        self._dispatched_child_count: int = 0

    def set_publisher(self, publisher: Any) -> None:
        self._publisher = publisher

    @property
    def dispatched_count(self) -> int:
        return self._dispatched_count

    @property
    def dispatched_child_count(self) -> int:
        return self._dispatched_child_count

    def on_run_started(self, event: RunStatusChanged) -> None:
        """Handle a ``RunStatusChanged(status="started")`` event for the top-level run."""
        try:
            self._dispatch_entries(
                event_id_seed=event.event_id or event.run_id,
                run_id=event.run_id,
                source="run_started",
            )
        except Exception:  # noqa: BLE001 — never break publish chain
            _logger.exception(
                "RunStartConsumer threw on RunStatusChanged run=%s — swallowed",
                event.run_id,
            )

    def on_child_run_started(self, event: ChildRunStarted) -> None:
        """Handle a ``ChildRunStarted`` event — drives entry-node
        dispatch for the child run identical to the top-level case."""
        try:
            self._dispatch_entries(
                event_id_seed=event.event_id or event.child_run_id,
                run_id=event.child_run_id,
                source="child_run_started",
                is_child=True,
            )
        except Exception:  # noqa: BLE001
            _logger.exception(
                "RunStartConsumer threw on ChildRunStarted child=%s — swallowed",
                getattr(event, "child_run_id", "?"),
            )

    def _dispatch_entries(
        self,
        *,
        event_id_seed: str,
        run_id: str,
        source: str,
        is_child: bool = False,
    ) -> None:
        if not run_id:
            return

        run = self._run_repo.get_by_id(run_id)
        if run is None:
            _logger.warning(
                "RunStartConsumer: run %s not found (source=%s)",
                run_id, source,
            )
            return
        workflow = self._workflow_repo.get_by_id(run.workflow_id)
        if workflow is None:
            _logger.warning(
                "RunStartConsumer: workflow %s not found (run=%s source=%s)",
                run.workflow_id, run_id, source,
            )
            return

        topology = getattr(getattr(run, "context", None), "topology", None)
        if topology is None or not getattr(topology, "entry_nodes", None):
            _logger.debug(
                "RunStartConsumer: no entry_nodes for run %s — workflow "
                "may have empty DAG", run_id,
            )
            return

        if self._publisher is None:
            _logger.warning(
                "RunStartConsumer: no publisher wired; entry nodes "
                "for run %s would not be dispatched", run_id,
            )
            return

        for node_id in topology.entry_nodes:
            node = workflow.get_node(node_id) if hasattr(workflow, "get_node") else None
            if node is None:
                _logger.warning(
                    "RunStartConsumer: entry node %s not in workflow %s",
                    node_id, workflow.id,
                )
                continue
            try:
                self._publisher.publish(
                    EventTopic.NODE_INPUT,
                    NodeInput(
                        event_id=EventId.derive(
                            event_id_seed, f"entry:{node_id}",
                        ),
                        run_id=run_id,
                        workflow_id=workflow.id,
                        node_id=node_id,
                        # task_id intentionally empty — TaskCreatorConsumer
                        # (Sub 4.1) creates the Task entity and stamps
                        # the deterministic id ``f"{run_id}-{node_id}"``.
                        task_id="",
                        resolved_input={},
                        root_run_id=run.root_run_id or run_id,
                        depth=run.depth_level,
                        source="run-start-consumer",
                    ),
                )
            except Exception:  # pragma: no cover — defensive
                _logger.exception(
                    "RunStartConsumer publish(NodeInput) failed run=%s "
                    "node=%s", run_id, node_id,
                )
                continue
            if is_child:
                self._dispatched_child_count += 1
            else:
                self._dispatched_count += 1


class RunStartTeeingPublisher:
    """Publisher decorator that feeds ``RunStatusChanged`` and
    ``ChildRunStarted`` to a ``RunStartConsumer`` synchronously.

    The consumer's own ``publish(NodeInput, ...)`` re-enters this
    publisher's ``publish`` — ``NodeInput`` is not in the tee's
    intercepted set, so no recursion.
    """

    def __init__(
        self,
        inner: Any,
        consumer: RunStartConsumer,
    ) -> None:
        self._inner = inner
        self._consumer = consumer

    def publish(self, topic: str, event: Any) -> None:
        self._inner.publish(topic, event)
        self._feed(topic, event)

    def publish_many(self, events: list) -> None:
        self._inner.publish_many(events)
        for entry in events:
            if isinstance(entry, tuple) and len(entry) == 2:
                self._feed(entry[0], entry[1])

    def _feed(self, topic: str, event: Any) -> None:
        try:
            if (
                topic == EventTopic.RUN_STATUS_CHANGED
                and isinstance(event, RunStatusChanged)
                and event.status == "started"
            ):
                self._consumer.on_run_started(event)
            elif (
                topic == EventTopic.CHILD_RUN_STARTED
                and isinstance(event, ChildRunStarted)
            ):
                self._consumer.on_child_run_started(event)
        except Exception:
            _logger.exception(
                "RunStartConsumer threw on event %s — swallowed",
                getattr(event, "event_id", "?"),
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
