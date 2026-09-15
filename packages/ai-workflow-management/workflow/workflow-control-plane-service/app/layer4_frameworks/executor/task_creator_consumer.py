"""Phase 4 Sub 4.1 — TaskCreatorConsumer.

Closes Gate 3 Gap 1 ([`phase-3-verification.md`](docs/design/phase-3-verification.md)
§3): when ``EXECUTOR_MODE=executor`` the engine emits ``NodeInput``
and returns; the dispatcher fans out and emits more ``NodeInput``
events for downstream nodes; but **no code path creates the ``Task``
entity for those downstream nodes** because task creation today
lives inside ``_exec_uniform``'s post-handler-result block which
executor mode bypasses.

This consumer fills the gap. It is a publisher-tee on the
``EventTopic.NODE_INPUT`` stream — it runs synchronously inside the
publish call so the ``Task`` entity is in the repo BEFORE the
broker delivers ``NodeInput`` to the standalone Executor pod. The
race that would otherwise have ``ExecutorConsumer`` invoke a
handler with ``task=None`` is eliminated by ordering: the tee runs
on the publishing pod, the broker fan-out happens after.

Task ids stay deterministic — ``TaskFactory.create_task`` already
returns ``f"{run_id}-{node_id}"``. Replay-safe: a re-emission of
the same ``NodeInput`` event re-computes the same task_id; the
consumer detects the existing task and short-circuits without
re-saving or re-emitting ``NodeDispatched``.

Mode gating: bootstrap wires the consumer only when
``EXECUTOR_MODE != "in_process"``. Under default ``in_process`` the
engine still creates tasks inline — running both paths would
double-emit ``NodeDispatched``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.events import (
    EventTopic,
    NodeInput,
    NodeDispatched,
)
from app.layer1_domain.value_objects.event_id import EventId


_logger = logging.getLogger(__name__)


class TaskCreatorConsumer:
    """Publisher-tee on ``NodeInput``; creates the Task and emits
    ``NodeDispatched`` for downstream nodes under executor mode.

    The consumer never raises — failures are logged and swallowed so
    the publish chain stays healthy. A poisoned NodeInput envelope
    cannot wedge the engine.
    """

    def __init__(
        self,
        *,
        task_repo: Any,
        workflow_repo: Any,
        run_repo: Any,
        task_factory: Any,
        publisher: Any | None = None,
    ) -> None:
        self._task_repo = task_repo
        self._workflow_repo = workflow_repo
        self._run_repo = run_repo
        self._task_factory = task_factory
        self._publisher = publisher
        # Counter for tests + metrics.
        self._created_count: int = 0
        self._skipped_existing_count: int = 0

    def set_publisher(self, publisher: Any) -> None:
        """Late-bind the publisher.

        Bootstrap wires the publisher chain in stages and the tee
        wraps an existing publisher; ``set_publisher`` lets the
        consumer pick up the final outer-most publisher reference
        once the chain is complete.
        """
        self._publisher = publisher

    @property
    def created_count(self) -> int:
        return self._created_count

    @property
    def skipped_existing_count(self) -> int:
        return self._skipped_existing_count

    def on_node_input(self, event: NodeInput) -> None:
        """Handle one ``NodeInput`` event.

        Skips silently when:
          - The envelope already carries a non-empty ``task_id``
            (meaning the engine already created the Task — the only
            time this happens is the first hop where the engine
            emits the entry-node ``NodeInput`` itself).
          - The run / workflow / node lookup fails (defensive log).
          - A Task with the deterministic id already exists (replay).
        """
        try:
            self._on_node_input(event)
        except Exception:  # noqa: BLE001 — never break the publish chain
            _logger.exception(
                "TaskCreatorConsumer threw on event %s — swallowed",
                getattr(event, "event_id", "?"),
            )

    def _on_node_input(self, event: NodeInput) -> None:
        if event.task_id:
            # Engine already created the Task (entry node, or in_process
            # mode). Nothing for the consumer to do.
            return

        run_id = event.run_id
        node_id = event.node_id
        if not run_id or not node_id:
            _logger.debug(
                "TaskCreatorConsumer dropping malformed NodeInput "
                "(run=%r node=%r)", run_id, node_id,
            )
            return

        run = self._run_repo.get_by_id(run_id)
        if run is None:
            _logger.warning(
                "TaskCreatorConsumer: run %s not found for NodeInput "
                "(node=%s)", run_id, node_id,
            )
            return
        workflow = self._workflow_repo.get_by_id(run.workflow_id)
        if workflow is None:
            _logger.warning(
                "TaskCreatorConsumer: workflow %s not found "
                "(run=%s node=%s)", run.workflow_id, run_id, node_id,
            )
            return
        node = workflow.get_node(node_id) if hasattr(workflow, "get_node") else None
        if node is None:
            _logger.warning(
                "TaskCreatorConsumer: node %s not found in workflow %s",
                node_id, workflow.id,
            )
            return

        # ``TaskFactory.create_task`` returns a deterministic id of the
        # shape ``f"{run.id}-{node.id}"`` — replays produce the same id
        # so we can short-circuit on an existing entry.
        candidate = self._task_factory.create_task(node, run)
        existing = self._task_repo.get_by_id(candidate.id)
        if existing is not None:
            terminal = {"COMPLETED", "FAILED", "CANCELLED"}
            status = getattr(existing.status, "value", existing.status)
            if str(status).upper() not in terminal:
                # In flight — coalesce / replay short-circuit.
                self._skipped_existing_count += 1
                return
            # SA-846: the latest task is terminal, so this NodeInput is a
            # revisit (cycle re-entry / late second arrival) and the node
            # must run again. Derive the revisit id from the causing
            # event so a redelivery of THIS NodeInput dedupes to the same
            # id, while a genuine later traversal (fresh event id) mints
            # a new one. Known gap: a redelivered NodeInput from a visit
            # that already completed under the base id will mint one
            # spurious revisit task — same at-least-once tradeoff the
            # task consumer already documents.
            suffix = (event.event_id or "")[:12]
            if not suffix:
                self._skipped_existing_count += 1
                return
            candidate.id = f"{candidate.id}-{suffix}"
            candidate.idempotency_key = f"{candidate.id}-attempt-0"
            if self._task_repo.get_by_id(candidate.id) is not None:
                self._skipped_existing_count += 1
                return

        # Save the Task BEFORE emitting NodeDispatched — consumers of
        # ``node.dispatched`` (the executor among them) expect the
        # repo lookup to succeed.
        self._task_repo.save(candidate)
        self._created_count += 1

        if self._publisher is None:
            return

        # Emit ``NodeDispatched`` so the projector + frontend learn
        # the task lifecycle has started. The deterministic event_id
        # is keyed on the source NodeInput so replays dedupe via the
        # event-log unique-constraint on ``(run_id, event_id)``.
        try:
            self._publisher.publish(
                EventTopic.NODE_DISPATCHED,
                NodeDispatched(
                    event_id=EventId.derive(
                        event.event_id or candidate.id, "task_dispatched",
                    ),
                    task_id=candidate.id,
                    run_id=run_id,
                    workflow_id=workflow.id,
                    node_id=node_id,
                    node_name=node.name,
                    worker_type=node.worker_type or "",
                    spec_version=getattr(node, "spec_version", None) or "",  # SA-1490 Phase 2
                    input=getattr(event, "resolved_input", None) or {},
                    node_config=node.config,
                    idempotency_key=candidate.idempotency_key or "",
                    correlation_id=run_id,
                    root_run_id=getattr(event, "root_run_id", "") or run_id,
                    depth=getattr(event, "depth", 0),
                    iteration_index=getattr(event, "iteration_index", None),
                    metadata=getattr(event, "metadata", {}) or {},
                    source="task-creator",
                    # Tracking-only: announces the task lifecycle to the
                    # projector/frontend. It carries the source NodeInput's
                    # ``resolved_input`` which, for entry/fan-out nodes, is
                    # empty (the handler hasn't resolved input yet). The
                    # authoritative worker dispatch comes from the engine
                    # (in_process) or the executor handler path — NOT here —
                    # so this must never be POSTed to the worker.
                    dispatch_to_worker=False,
                ),
            )
        except Exception:  # pragma: no cover — defensive
            _logger.exception(
                "TaskCreatorConsumer publish(NodeDispatched) failed "
                "for task %s", candidate.id,
            )


class TaskCreatorTeeingPublisher:
    """Publisher decorator that feeds ``NodeInput`` to a
    ``TaskCreatorConsumer`` synchronously.

    Wraps the existing publisher chain. ``publish``/``publish_many``
    forward to the inner publisher first, then call
    ``consumer.on_node_input`` so the Task save + ``NodeDispatched``
    re-publish happen on the same call frame. The consumer's own
    ``publish(NodeDispatched, ...)`` re-enters this publisher's
    ``publish`` — ``NodeDispatched`` is not ``NodeInput`` so the tee
    no-ops on it, no infinite recursion.
    """

    def __init__(
        self,
        inner: Any,
        consumer: TaskCreatorConsumer,
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
            if topic == EventTopic.NODE_INPUT and isinstance(event, NodeInput):
                self._consumer.on_node_input(event)
        except Exception:
            _logger.exception(
                "TaskCreatorConsumer threw on event %s — swallowed",
                getattr(event, "event_id", "?"),
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
