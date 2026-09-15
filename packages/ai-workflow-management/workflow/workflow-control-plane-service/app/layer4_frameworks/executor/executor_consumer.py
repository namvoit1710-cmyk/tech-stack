"""ExecutorConsumer — broker-subscribed handler invoker.

Subscribes to ``EventTopic.NODE_INPUT``. Receives ``NodeInput`` messages
emitted by the Dispatcher, resolves the handler from the registry,
invokes it, and emits ``NodeOutputProduced`` / ``NodeCompleted`` /
``NodeFailed`` events authoritatively.

Active only under ``EXECUTOR_MODE=executor``; under the default
``in_process`` the consumer is constructed but ``start()`` is a no-op
(the engine invokes handlers inline). The ``executor`` emit path is a
thin "would-emit" surface — the engine still emits authoritatively
until the Gate 3 inline-deletion surgery lands (doc §13.4).

Per-pod LRU dedup short-circuits redelivered ``NodeInput`` messages.
Cross-pod dedupe is handled by the event log (deterministic event_ids
+ ``(run_id, event_id)`` unique constraint).

Memory mode (``broker is None``): the consumer falls back to a
direct-notify ``notify(envelope)`` path so the publisher tee can drive
unit tests without a broker.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Callable
from typing import Any

from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer4_frameworks.executor.executor_dedup import ExecutorDedup


_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutorResult:
    """Outcome of one handler invocation, keyed by
    ``(run_id, node_id, attempt)``. Drives the authoritative emit path
    in ``executor`` mode.
    """

    run_id: str
    node_id: str
    attempt: int
    event_types: tuple[str, ...]
    output_port: str
    error: str = ""


class ExecutorMode(str, Enum):
    """``EXECUTOR_MODE`` values."""

    IN_PROCESS = "in_process"
    EXECUTOR = "executor"


class ExecutorConsumer:
    """Broker-subscribed handler invoker for ``node.input``.

    Construction is cheap; ``start()`` subscribes to the broker and
    captures the asyncio loop. The loop reference is needed so the
    broker callback (which runs on the broker client's polling thread
    in Kafka mode) can hand work to the asyncio handler invocation
    via ``call_soon_threadsafe``.
    """

    def __init__(
        self,
        *,
        handler_registry: Any,
        run_repo: Any,
        workflow_repo: Any,
        task_repo: Any,
        task_factory: Any,
        broker: Any | None = None,
        dedup: ExecutorDedup | None = None,
        mode: str = ExecutorMode.IN_PROCESS.value,
        publisher: Any | None = None,
    ) -> None:
        self._handler_registry = handler_registry
        self._run_repo = run_repo
        self._workflow_repo = workflow_repo
        self._task_repo = task_repo
        self._task_factory = task_factory
        self._broker = broker
        self._dedup = dedup or ExecutorDedup()
        self._mode = mode
        self._publisher = publisher
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)
        self._stopped = asyncio.Event()
        # Counter for tests + metrics. Incremented after each handler
        # invocation regardless of outcome.
        self._invocations: int = 0

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def invocations(self) -> int:
        return self._invocations

    @property
    def is_active(self) -> bool:
        """``True`` when the consumer should actually invoke handlers."""
        return self._mode == ExecutorMode.EXECUTOR.value

    def start(self) -> None:
        """Subscribe to ``node.input`` and capture the running loop.

        Must be called from the FastAPI lifespan task (asyncio main
        thread). No-op when ``mode == in_process`` so the bootstrap can
        wire the consumer unconditionally and feature-flag at the
        single ``start()`` call site.
        """
        if not self.is_active:
            _logger.info(
                "ExecutorConsumer not started (mode=%s)", self._mode,
            )
            return
        self._loop = asyncio.get_running_loop()

        if self._broker is None:
            _logger.info(
                "ExecutorConsumer in direct-notify mode (no broker — "
                "tests / mock infra). Publisher tee or direct ``notify`` "
                "delivers ``NodeInput`` envelopes.",
            )
            return

        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(EventTopic.NODE_INPUT)
            except Exception:
                _logger.warning(
                    "ExecutorConsumer could not auto-create topic %s",
                    EventTopic.NODE_INPUT,
                )

        def _on_message(message: dict[str, Any]) -> None:
            if self._loop is None or self._loop.is_closed():
                _logger.warning(
                    "ExecutorConsumer event loop unavailable — dropping",
                )
                return
            try:
                self._loop.call_soon_threadsafe(
                    self._queue.put_nowait, message,
                )
            except asyncio.QueueFull:
                _logger.warning(
                    "ExecutorConsumer queue full — dropping NodeInput",
                )

        self._broker.subscribe_to_topic(EventTopic.NODE_INPUT, _on_message)
        _logger.info(
            "ExecutorConsumer subscribed to %s (mode=%s)",
            EventTopic.NODE_INPUT, self._mode,
        )

    def notify(self, envelope: dict[str, Any]) -> None:
        """Direct-notify path for memory/test mode.

        Bypasses the broker — the publisher tee or test driver hands
        the envelope to the consumer in-process. Mirrors
        ``SocketIORealtimeConsumer.notify``.

        When the caller is already running inside the captured event
        loop (typical for in-process tests + memory mode), short-circuit
        to a direct ``put_nowait`` so a same-tick ``drain_once`` sees
        the envelope without needing an extra ``await asyncio.sleep(0)``
        round-trip.
        """
        if not self.is_active:
            return
        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is self._loop:
                self._queue.put_nowait(envelope)
            else:
                self._loop.call_soon_threadsafe(
                    self._queue.put_nowait, envelope,
                )
        except asyncio.QueueFull:
            _logger.warning(
                "ExecutorConsumer notify queue full — dropping NodeInput",
            )

    async def run_forever(self) -> None:
        """Drain the in-flight queue until ``stop()`` is called."""
        if not self.is_active:
            return
        _logger.info("ExecutorConsumer run_forever started (mode=%s)", self._mode)
        while not self._stopped.is_set():
            try:
                envelope = await asyncio.wait_for(
                    self._queue.get(), timeout=0.5,
                )
            except asyncio.TimeoutError:
                continue
            try:
                await self._on_envelope(envelope)
            except Exception:  # pragma: no cover — defensive
                _logger.exception("ExecutorConsumer iteration failed")

    async def drain_once(self) -> int:
        """Process every queued envelope once. Used by tests to step
        the consumer without spawning ``run_forever``.
        """
        processed = 0
        while not self._queue.empty():
            envelope = self._queue.get_nowait()
            await self._on_envelope(envelope)
            processed += 1
        return processed

    def stop(self) -> None:
        self._stopped.set()

    # ─────────────── envelope handling ───────────────

    async def _on_envelope(self, envelope: dict[str, Any]) -> None:
        """Decode a ``NodeInput`` envelope and invoke its handler.

        ``envelope`` is the broker-published dict with keys including
        ``run_id``, ``workflow_id``, ``node_id``, ``task_id``,
        ``event_type``. The publisher chain serialises ``NodeInput``
        events to this shape via ``EnrichingEventPublisher``.
        """
        run_id = str(envelope.get("run_id") or "")
        node_id = str(envelope.get("node_id") or "")
        task_id = str(envelope.get("task_id") or "")
        workflow_id = str(envelope.get("workflow_id") or "")
        attempt = int(envelope.get("attempt") or 0)
        if not run_id or not node_id:
            _logger.warning(
                "ExecutorConsumer dropping malformed envelope: %s", envelope,
            )
            return
        if self._dedup.already_executed(run_id, node_id, attempt):
            _logger.debug(
                "ExecutorConsumer dedup hit run=%s node=%s attempt=%d",
                run_id, node_id, attempt,
            )
            return

        record, handler_result = await self._invoke_and_classify(
            run_id=run_id, node_id=node_id, attempt=attempt, envelope=envelope,
        )
        self._dedup.mark_executed(run_id, node_id, attempt)
        self._invocations += 1

        if self._mode == ExecutorMode.EXECUTOR.value:
            await self._publish_authoritative(
                record=record,
                handler_result=handler_result,
                run_id=run_id,
                workflow_id=workflow_id,
                node_id=node_id,
                task_id=task_id,
            )

    async def _invoke_and_classify(
        self,
        *,
        run_id: str,
        node_id: str,
        attempt: int,
        envelope: dict[str, Any],
    ) -> tuple[ExecutorResult, Any]:
        """Look up the handler and invoke it. Build the parity record.

        Returns ``(record, handler_result)``. ``handler_result`` is
        ``None`` when invocation failed (the parity record's ``error``
        field carries the reason).

        On any exception (missing run/workflow/node, handler raise),
        produce a record with ``error=str(exc)`` so the parity
        comparator can flag it. The Executor never crashes on a single
        bad envelope; that would mean a poison message could halt the
        entire Executor pod.
        """
        try:
            run = self._run_repo.get_by_id(run_id)
            if run is None:
                return _error_record(run_id, node_id, attempt, "run_not_found"), None
            workflow = self._workflow_repo.get_by_id(run.workflow_id)
            if workflow is None:
                return _error_record(run_id, node_id, attempt, "workflow_not_found"), None
            node = workflow.get_node(node_id) if hasattr(workflow, "get_node") else None
            if node is None:
                return _error_record(run_id, node_id, attempt, "node_not_found"), None

            handler = self._handler_registry.get(node.node_type)
            task_id = str(envelope.get("task_id") or "")
            task = self._task_repo.get_by_id(task_id) if task_id else None
            topology = getattr(getattr(run, "context", None), "topology", None)

            handler_result = await handler.handle(
                node=node,
                run=run,
                workflow=workflow,
                topology=topology,
                completed_task=task,
                task_factory=self._task_factory,
            )
            event_types = tuple(
                type(e).__name__ for e in (handler_result.events or [])
            )
            output_port = getattr(handler_result, "output_port", "") or ""
            record = ExecutorResult(
                run_id=run_id, node_id=node_id, attempt=attempt,
                event_types=event_types,
                output_port=output_port,
                error="",
            )
            return record, handler_result
        except Exception as exc:  # noqa: BLE001 — recorded into parity
            _logger.warning(
                "ExecutorConsumer handler invocation failed run=%s node=%s: %s",
                run_id, node_id, exc,
            )
            return _error_record(run_id, node_id, attempt, str(exc)), None

    async def _publish_authoritative(
        self,
        *,
        record: ExecutorResult,
        handler_result: Any,
        run_id: str,
        workflow_id: str,
        node_id: str,
        task_id: str,
    ) -> None:
        """Authoritative emission path (``executor`` mode — Gate 3).

        Publishes:
          1. ``NodeOutputProduced`` — drives the Dispatcher's fan-out
             and the projector's variables/completed_nodes folding.
          2. Every event in ``handler_result.events`` (handler-specific
             events — ``InputRequested``, ``HumanActionCompleted``,
             ``LoopIterationCompleted``, etc.).
          3. ``NodeCompleted`` (or ``NodeFailed`` when ``record.error``
             is non-empty) so downstream consumers and the projector
             see the task lifecycle terminate.

        Idempotent under broker redelivery: every event id is derived
        from a deterministic seed (``task_id`` + suffix), so the event
        log dedupes redeliveries on ``(run_id, event_id)``.

        Failures inside the publisher chain are caught and logged so
        a poisoned downstream consumer cannot wedge the Executor's
        invocation loop.
        """
        # Transition the node's own Task entity to a terminal status.
        # ``check_run_completion`` decides run quiescence purely from
        # task status (see orchestration_engine.py §check_run_completion),
        # so without this the task stays PENDING and the run never
        # completes. Done before the publisher-None guard because it is a
        # repo mutation, not a publish.
        completion_mode = (
            getattr(handler_result, "completion_mode", "immediate")
            if handler_result is not None
            else "immediate"
        )
        if record.error:
            self._mark_task_terminal(
                run_id=run_id, node_id=node_id, task_id=task_id,
                status=TaskStatus.FAILED, error=record.error,
            )
        elif completion_mode in ("immediate", "absorbed"):
            self._mark_task_terminal(
                run_id=run_id, node_id=node_id, task_id=task_id,
                status=TaskStatus.COMPLETED,
            )
        # "dispatched" (TASK → worker) and pending modes are intentionally
        # NOT marked here — the worker callback (CompleteTaskUseCase) or
        # the HITL resume path owns terminating those tasks.

        if self._publisher is None:
            return

        # "absorbed" — straggler arrival at an already-fired MERGE quorum.
        # Terminal but silent: publishing NodeOutputProduced here would
        # make the DispatcherConsumer tee EdgeTraversed and re-route the
        # downstream subtree, which is exactly what absorb prevents.
        # (Handler errors still fall through to the NodeFailed publish.)
        if completion_mode == "absorbed" and not record.error:
            return

        from app.layer1_domain.events import NodeOutputProduced, NodeCompleted, NodeFailed
        from app.layer1_domain.value_objects.event_id import EventId

        seed = task_id or node_id

        try:
            if record.error:
                # Handler raised — emit ``NodeFailed`` instead of
                # ``NodeOutputProduced``. The projector folds this into
                # ``run.status`` / failed-task tracking.
                self._publish_one(
                    EventTopic.NODE_FAILED,
                    NodeFailed(
                        event_id=EventId.derive(seed, "task_failed"),
                        run_id=run_id,
                        workflow_id=workflow_id,
                        task_id=task_id,
                        node_id=node_id,
                        error_message=record.error,
                    ),
                )
                return

            if handler_result is None:
                return

            # 1. NodeOutputProduced — primary downstream signal.
            # Strict wire-thin: drop ``extracted`` from every file_ref so the
            # event stays under broker per-message limits. Mirror of the
            # ``apply_state_updates_as_files`` engine path.
            state_updates = getattr(handler_result, "state_updates", {}) or {}
            self._publish_one(
                EventTopic.NODE_OUTPUT_PRODUCED,
                NodeOutputProduced(
                    event_id=EventId.derive(seed, "node_output"),
                    run_id=run_id,
                    workflow_id=workflow_id,
                    node_id=node_id,
                    state_updates=state_updates,
                    output_port=record.output_port or "out_default",
                    taken_edge_ports=getattr(
                        handler_result, "taken_edge_ports", {},
                    ) or {},
                    source="wio-executor",
                ),
            )

            # 2. Handler-specific events.
            for event in (getattr(handler_result, "events", None) or []):
                topic = self._topic_for_event(event)
                if topic is None:
                    continue
                self._publish_one(topic, event)

            # 3. NodeCompleted (skip when handler enters a non-terminal
            # completion mode such as ``pending`` / ``loop_body`` — the
            # task lifecycle continues elsewhere).
            completion_mode = getattr(handler_result, "completion_mode", "immediate")
            if completion_mode in ("immediate", "dispatched"):
                self._publish_one(
                    EventTopic.NODE_COMPLETED,
                    NodeCompleted(
                        event_id=EventId.derive(seed, "task_completed"),
                        run_id=run_id,
                        workflow_id=workflow_id,
                        task_id=task_id,
                        node_id=node_id,
                    ),
                )
        except Exception:  # noqa: BLE001 — never break the invoke loop
            _logger.exception(
                "ExecutorConsumer authoritative publish failed run=%s node=%s",
                run_id, node_id,
            )

    def _mark_task_terminal(
        self,
        *,
        run_id: str,
        node_id: str,
        task_id: str,
        status: TaskStatus,
        error: str = "",
    ) -> None:
        """Set the node's Task entity to a terminal status, idempotently.

        Entry-node and dispatcher-emitted ``NodeInput`` envelopes carry
        an empty ``task_id`` (the TaskCreatorConsumer stamps the
        deterministic ``f"{run_id}-{node_id}"`` id), so resolve that id
        when the envelope didn't carry one. A no-op if the task is
        missing or already terminal — so broker redeliveries don't
        clobber a completed task or its ``completed_at``.
        """
        resolved_id = task_id or f"{run_id}-{node_id}"
        try:
            task = self._task_repo.get_by_id(resolved_id)
        except Exception:  # pragma: no cover — defensive
            task = None
        if task is None:
            _logger.debug(
                "ExecutorConsumer: no Task %s to mark %s (run=%s node=%s)",
                resolved_id, status.value, run_id, node_id,
            )
            return
        if task.status in (
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED,
        ):
            return
        task.status = status
        task.completed_at = datetime.now(timezone.utc).isoformat()
        if error:
            task.error = error
        try:
            self._task_repo.save(task)
        except Exception:  # pragma: no cover — defensive
            _logger.exception(
                "ExecutorConsumer failed to persist terminal task %s",
                resolved_id,
            )

    def _publish_one(self, topic: str, event: Any) -> None:
        """Publish a single event via the wired publisher.

        The publisher is duck-typed: prod uses the EnrichingEventPublisher
        chain (``publish(topic, event)``); tests substitute MagicMock
        with the same interface.
        """
        publish = getattr(self._publisher, "publish", None)
        if not isinstance(publish, Callable):
            return
        publish(topic, event)

    def _topic_for_event(self, event: Any) -> str | None:
        """Map a domain event class to its canonical topic string.

        We avoid an event-type → topic dispatch table; the mapping is
        already encoded in ``EventTopic`` constants. The fallback is
        ``snake_case(ClassName)`` which matches the naming convention
        across the codebase (``RunStarted`` → ``"run.started"``).
        """
        from app.layer1_domain.events import topic_for_event

        return topic_for_event(event)


def _error_record(
    run_id: str, node_id: str, attempt: int, message: str,
) -> ExecutorResult:
    return ExecutorResult(
        run_id=run_id, node_id=node_id, attempt=attempt,
        event_types=(), output_port="", error=message,
    )
