"""Phase 4 — RunCompletionConsumer.

Closes the executor-mode completion gap: under ``EXECUTOR_MODE=executor``
the engine bows out of handler invocation and ``return``s before its
inline ``check_run_completion`` for every post-entry node. The
DispatcherConsumer owns fan-out, and the ExecutorConsumer now marks each
node's Task entity terminal — but nothing re-evaluates run quiescence, so
the run hangs in RUNNING after the leaf node finishes.

This consumer fills that gap. It is a publisher-tee on the
``NodeCompleted`` / ``NodeFailed`` streams — on each terminal node event
it enqueues a ``CheckRunCompletionAction`` so the OrchestrationWorker
runs ``orchestrate_check_completion`` (a completion-ONLY path; it does
NOT fan out). ``check_run_completion`` is quiescence-gated and
idempotent, so a check that fires before the run is done is a safe no-op
and only the final node's check transitions the run to COMPLETED /
FAILED.

Failure-edge routing (``on_failure`` handlers via ``orchestrate_fail``)
is a SEPARATE concern and is deliberately NOT handled here — a
``NodeFailed`` here only drives the run toward its terminal FAILED state
via ``check_run_completion``'s ``has_failed`` branch. Wiring failure
edges into the executor-mode path is future work.

Race note: completing on an intermediate node before its successor Task
exists would prematurely finish the run. In the combined-pod topology
the executor publishes through the in-process tee chain, so a node's
``NodeOutputProduced`` synchronously drives Dispatcher → TaskCreator
(successor tasked) BEFORE its ``NodeCompleted`` reaches this tee — that
happens-before makes the enqueued check safe. When the consumers become
broker subscribers (a true process split) that ordering no longer holds
and this path must be revisited.

Mode gating: bootstrap wires the consumer only when
``EXECUTOR_MODE != "in_process"``. Under default ``in_process`` the
engine still drives completion inline.
"""

from __future__ import annotations

import logging
from typing import Any

from app.layer1_domain.events import EventTopic, NodeCompleted, NodeFailed
from app.layer2_application.interfaces.orchestration_queue_interface import (
    CheckRunCompletionAction,
)


_logger = logging.getLogger(__name__)


class RunCompletionConsumer:
    """Publisher-tee on ``NodeCompleted`` + ``NodeFailed``; enqueues a
    completion check so the run reaches a terminal state.

    Like the other publisher-tee consumers, this one never raises —
    failures are logged and swallowed so the publish chain stays healthy.
    """

    def __init__(self, *, queue: Any | None = None) -> None:
        self._queue = queue
        self._enqueued_count: int = 0

    def set_queue(self, queue: Any) -> None:
        """Late-bind the orchestration queue.

        The queue is constructed after the publisher chain in bootstrap,
        so the consumer is wired first and the queue injected once it
        exists (mirrors ``OrchestrationEngine.set_queue``).
        """
        self._queue = queue

    @property
    def enqueued_count(self) -> int:
        return self._enqueued_count

    def on_node_terminal(self, event: NodeCompleted | NodeFailed) -> None:
        """Enqueue a completion check for the event's run."""
        run_id = getattr(event, "run_id", "") or ""
        if not run_id:
            return
        if self._queue is None:
            _logger.warning(
                "RunCompletionConsumer: no queue wired; completion check "
                "for run %s would not be enqueued", run_id,
            )
            return
        try:
            # Synchronous enqueue — this runs inside a publisher-tee
            # ``_feed`` which cannot ``await``.
            self._queue.enqueue_nowait(CheckRunCompletionAction(run_id=run_id))
            self._enqueued_count += 1
        except Exception:  # noqa: BLE001 — never break the publish chain
            _logger.exception(
                "RunCompletionConsumer failed to enqueue completion check "
                "for run %s", run_id,
            )


class RunCompletionTeeingPublisher:
    """Publisher decorator that feeds ``NodeCompleted`` / ``NodeFailed``
    to a ``RunCompletionConsumer`` synchronously.

    The consumer only enqueues onto the orchestration queue (it never
    re-publishes), so there is no recursion risk.
    """

    def __init__(self, inner: Any, consumer: RunCompletionConsumer) -> None:
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
            if topic == EventTopic.NODE_COMPLETED and isinstance(event, NodeCompleted):
                self._consumer.on_node_terminal(event)
            elif topic == EventTopic.NODE_FAILED and isinstance(event, NodeFailed):
                self._consumer.on_node_terminal(event)
        except Exception:
            _logger.exception(
                "RunCompletionConsumer threw on event %s — swallowed",
                getattr(event, "event_id", "?"),
            )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
