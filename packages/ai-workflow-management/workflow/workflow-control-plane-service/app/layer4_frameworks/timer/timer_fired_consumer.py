"""Resume WAIT nodes when their durable timer fires (SA-1371 S4).

The leader-gated ``TimerDispatcher`` publishes ``TimerFired`` on the
``timer.fired`` topic when a timer's ``fire_at`` elapses. This consumer
subscribes to that topic and, for a ``TimerFired`` whose node is a ``WAIT``
node, resumes the run through the existing ``resume_run`` use case — the same
path an ``InputSubmitted`` user-submit of an INPUT node takes to un-park a run.

Mirrors ``WorkerResultsConsumer``: the broker poll thread invokes the callback,
which bridges the work onto the captured main event loop so the use case runs
in the same async context as the rest of WCP.

Idempotency
===========

``resume_run`` fans out to the run's PENDING tasks via ``resume_pending_task``,
which rejects any task that is no longer ``PENDING`` (``INVALID_TASK_STATUS``).
The first ``TimerFired`` completes the WAIT task; a *duplicate* ``TimerFired``
(two pods, broker redelivery) therefore finds the task already COMPLETED and
resolves to a safe no-op — no double-dispatch of successors. The upstream
``TimerFired`` event-id is also deterministic, so the event log dedupes the
redelivery before it ever reaches a second resume in most topologies.

The WAIT-node guard means this consumer ignores timers scheduled against any
non-WAIT node (e.g. a future SLA timer on a TASK), so it is safe to run
alongside other timer consumers.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.layer1_domain.events import EventTopic
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.features.resume_run.use_cases.resume_run_use_case import (
    ResumeRunCommand,
)

logger = logging.getLogger(__name__)

TIMER_FIRED_TOPIC = str(EventTopic.TIMER_FIRED)


class TimerFiredConsumer:
    """Consumes ``timer.fired`` and resumes the WAIT node that scheduled it."""

    def __init__(self, container: dict, broker: Any) -> None:
        self._container = container
        self._broker = broker
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribed = False

    def start(self) -> None:
        """Subscribe to the timer.fired topic. Call from the main asyncio thread."""
        if self._subscribed:
            return
        if self._broker is None:
            logger.debug("No broker — TimerFiredConsumer inactive")
            return
        self._subscribed = True
        self._loop = asyncio.get_running_loop()

        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(TIMER_FIRED_TOPIC)
            except Exception:
                logger.warning("Could not auto-create topic '%s'", TIMER_FIRED_TOPIC)

        self._broker.subscribe_to_topic(TIMER_FIRED_TOPIC, self._on_message)
        logger.info("Timer fired consumer subscribed to topic: %s", TIMER_FIRED_TOPIC)

    def _on_message(self, message: dict[str, Any]) -> None:
        if self._loop is None or self._loop.is_closed():
            logger.warning("Main event loop unavailable — dropping timer.fired: %s", message)
            return
        # Run the async handler and WAIT for it so the broker only acks after
        # processing succeeds; exceptions propagate to the poll loop so the
        # message is redelivered (at-least-once). Redelivery is safe: the
        # resume is idempotent (a completed WAIT task rejects a second resume).
        future = asyncio.run_coroutine_threadsafe(self._handle(message), self._loop)
        future.result()

    async def _handle(self, message: dict[str, Any]) -> None:
        payload = message.get("payload", message) or {}
        run_id = payload.get("run_id") or message.get("run_id") or message.get("correlation_id", "")
        node_id = payload.get("node_id") or message.get("node_id", "")

        if not run_id or not node_id:
            logger.warning("timer.fired message missing run_id/node_id: %s", message)
            return

        # Guard: only WAIT nodes are resumed by this consumer. A timer fired
        # against any other node type is ignored here (some other subsystem
        # owns it) so we never mis-resume a run.
        if not await self._is_wait_node(run_id, node_id):
            logger.debug(
                "timer.fired for run=%s node=%s is not a WAIT node — ignoring",
                run_id, node_id,
            )
            return

        uc = self._container.get("resume_run")
        if uc is None:
            logger.error("resume_run use case not available in container")
            return

        result = await uc.execute(ResumeRunCommand(run_id=run_id))
        if getattr(result, "resumed", False):
            logger.info(
                "Resumed run=%s from WAIT node=%s on timer.fired (tasks=%s)",
                run_id, node_id, getattr(result, "resumed_task_ids", []),
            )
        else:
            # Not an error: a duplicate TimerFired (task already completed) or a
            # terminal run resolves to a no-op here.
            logger.debug(
                "timer.fired resume no-op for run=%s node=%s: %s",
                run_id, node_id, getattr(result, "error", "") or "no waiters",
            )

    async def _is_wait_node(self, run_id: str, node_id: str) -> bool:
        run_repo = self._container.get("run_repo")
        workflow_repo = self._container.get("workflow_repo")
        if run_repo is None or workflow_repo is None:
            return False
        run = await run_repo.aget_by_id(run_id)
        if run is None:
            return False
        workflow = await workflow_repo.aget_by_id(run.workflow_id)
        if workflow is None:
            return False
        node = workflow.get_node(node_id)
        return node is not None and node.node_type == NodeType.WAIT
