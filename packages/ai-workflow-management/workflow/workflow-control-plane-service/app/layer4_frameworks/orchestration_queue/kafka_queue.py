"""Broker-backed orchestration queue (Kafka / SAP Event Mesh).

Publishes orchestration actions to a message broker topic and consumes
them in a background thread.  Uses run_id as the partition key so that
actions for the same run are processed in order.

Works with any broker client that implements ``publish_to_topic()`` and
``subscribe_to_topic()`` — both ``KafkaBrokerClient`` and
``EventMeshBrokerClient`` satisfy this contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import dataclasses
from dataclasses import asdict
from typing import Any

from app.layer2_application.interfaces.broker_client_interface import IBrokerClient
from app.layer2_application.interfaces.orchestration_queue_interface import (
    IOrchestrationQueue,
    OrchestrationAction,
    StartRunAction,
    ContinueRunAction,
    FailTaskAction,
    ResumeTaskAction,
    ExecuteNodeAction,
    LoopIterationAction,
    ResumeRunAction,
    EditNodeDataAction,
    CheckRunCompletionAction,
)

_logger = logging.getLogger(__name__)

ORCHESTRATION_TOPIC = "orchestration.actions"

_ACTION_CLASSES: dict[str, type[OrchestrationAction]] = {
    "start": StartRunAction,
    "continue": ContinueRunAction,
    "fail": FailTaskAction,
    "resume": ResumeTaskAction,
    "execute_node": ExecuteNodeAction,
    "loop_iteration": LoopIterationAction,
    "resume_run": ResumeRunAction,
    "edit_node_data": EditNodeDataAction,
    "check_completion": CheckRunCompletionAction,
}


class BacklogFull(RuntimeError):
    """The in-memory action backlog is full; the message must NOT be acked.

    Raised from the broker callback on purpose: ``_dispatch_to_callbacks``
    ignores a handler's return value and only treats an exception as failure,
    and failure is what leaves the message unsettled for redelivery.
    """


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "")
    if not raw:
        return max(minimum, default)
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        _logger.warning("%s=%r is not an integer — using %d", name, raw, default)
        return max(minimum, default)


# Slice of the pod's RAM budget for the un-processed orchestration backlog, and
# a pessimistic per-action footprint. An OrchestrationAction is a small
# dataclass (ids, a type, occasionally an inline payload); 4KB is a round
# number chosen high rather than low, because the cost of guessing low here is
# refusing work that would have fit.
_BACKLOG_BUDGET_BYTES = _int_env("ORCHESTRATION_BACKLOG_BUDGET_BYTES", 8 * 1024 * 1024)
_ACTION_SIZE_ESTIMATE_BYTES = _int_env("ORCHESTRATION_ACTION_SIZE_ESTIMATE_BYTES", 4096)


def orchestration_backlog_maxsize() -> int:
    """Bound for the in-memory action backlog. ``ORCHESTRATION_BACKLOG_MAX`` wins."""
    override = os.getenv("ORCHESTRATION_BACKLOG_MAX", "")
    if override:
        try:
            return max(1, int(override))
        except (TypeError, ValueError):
            _logger.warning(
                "ORCHESTRATION_BACKLOG_MAX=%r is not an integer — deriving from "
                "the memory budget instead", override,
            )
    return max(1, _BACKLOG_BUDGET_BYTES // _ACTION_SIZE_ESTIMATE_BYTES)


class _BacklogStats:
    """Depth + refusals for the action backlog, exported via SA-2256.

    ``__weakref__`` is REQUIRED — the registry holds members weakly and
    ``__slots__`` without it makes the class un-weak-referenceable. Registration
    happens inside a try/except so observability can never block boot, which
    means that failure would be SILENT and the metric would simply never
    appear. Third time this trap has been hit in SA-2254.
    """

    __slots__ = ("_queue", "_maxsize", "_refused", "__weakref__")

    def __init__(self, queue: Any, maxsize: int) -> None:
        self._queue = queue
        self._maxsize = maxsize
        self._refused = 0

    def record_refusal(self) -> None:
        self._refused += 1

    @property
    def refused(self) -> int:
        return self._refused

    def budget_snapshot(self) -> dict:
        try:
            depth = self._queue.qsize()
        except Exception:  # pragma: no cover - defensive
            depth = -1
        return {"entries": depth, "maxsize": self._maxsize, "refused": self._refused}


class BrokerOrchestrationQueue(IOrchestrationQueue):
    """Publish actions to a message broker; consume via a background thread into an asyncio.Queue."""

    def __init__(self, broker_client: IBrokerClient, monitor: Any = None) -> None:
        self._broker = broker_client
        self._monitor = monitor
        # BOUNDED. This was a plain ``asyncio.Queue()`` with no maxsize, and
        # the broker callback ACKS on enqueue (it returns immediately rather
        # than waiting for the action to run — see ``_on_message``). Two
        # consequences, both worse the more throughput the pod is given:
        # a burst grew the backlog without limit inside a 1 GB pod, and every
        # action in it was already acked, so a crash lost them with no
        # redelivery. Bounding it converts both into broker backpressure.
        maxsize = orchestration_backlog_maxsize()
        self._internal: asyncio.Queue[OrchestrationAction] = asyncio.Queue(
            maxsize=maxsize,
        )
        self.backlog_stats = _BacklogStats(self._internal, maxsize)
        try:
            from app.layer2_application.interfaces.cache_budget_registry import (
                default_cache_budget_registry,
            )
            default_cache_budget_registry().register(
                self.backlog_stats, "orchestration_backlog",
            )
        except Exception:  # pragma: no cover — observability must never block boot
            _logger.debug("orchestration backlog stats not registered", exc_info=True)
        self._subscribed = False
        self._topic_ensured = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def start_consumer(self) -> None:
        """Ensure the topic exists, then subscribe.

        For Kafka, calls ``ensure_topic`` to auto-create the topic.
        For Event Mesh, ``subscribe_to_topic`` already creates the
        queue and subscription internally.

        Messages are pushed into the internal asyncio queue so the
        :class:`OrchestrationWorker` can ``await dequeue()`` normally.
        """
        if self._subscribed:
            return
        self._subscribed = True

        # Capture the main thread's event loop so the consumer-thread
        # callback can bridge messages into the asyncio world.
        self._loop = asyncio.get_running_loop()

        # Auto-create topic for Kafka; Event Mesh handles this in subscribe_to_topic
        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(ORCHESTRATION_TOPIC)
            except Exception:
                _logger.warning(
                    "Could not auto-create topic '%s' — will subscribe anyway",
                    ORCHESTRATION_TOPIC,
                )

        def _on_message(message: dict[str, Any]) -> None:
            action = _deserialize(message)
            if action is None:
                _logger.warning("Could not deserialize orchestration message: %s", message)
                return
            # Bridge from the broker consumer thread to the main asyncio event loop.
            if self._loop is None or self._loop.is_closed():
                _logger.warning("Main event loop not available — dropping message: %s", message)
                return
            # The backlog is bounded, so it can refuse. RAISE on a full queue:
            # a sync handler's RETURN VALUE is ignored by
            # ``_dispatch_to_callbacks`` — only an exception marks the message
            # unhandled, which is what leaves it unsettled for redelivery after
            # the lock TTL. Dropping instead would be a permanently hung run
            # every time a StartRunAction lost the race, which is the exact
            # failure mode the whole AMQP workstream exists to prevent.
            if self._internal.full():
                self.backlog_stats.record_refusal()
                _logger.warning(
                    "Orchestration backlog full (%d) — refusing %s for run %s so "
                    "the broker redelivers it. Raise ORCHESTRATION_BACKLOG_MAX "
                    "only with the pod's memory headroom in hand.",
                    self._internal.maxsize, action.action_type, action.run_id,
                )
                raise BacklogFull(
                    f"orchestration backlog full ({self._internal.maxsize})"
                )
            self._observe_hop(action)
            self._loop.call_soon_threadsafe(self._internal.put_nowait, action)

        self._broker.subscribe_to_topic(ORCHESTRATION_TOPIC, _on_message)
        _logger.info("Subscribed to broker topic: %s", ORCHESTRATION_TOPIC)

    def _observe_hop(self, action: OrchestrationAction) -> None:
        """Record how long this action spent getting back to us.

        The control plane publishes orchestration actions to Event Mesh and
        consumes them in the SAME process, so one wall clock covers both ends
        and the difference is the whole hop: send-buffer wait, HTTPS POST,
        broker fan-out, AMQP delivery, and the consumer thread picking it up.

        This exists because that hop was measured at 0.065 s once and 0.185 s
        another time in the same run, and the explanation offered for the gap —
        queueing in the send buffer — turned out to be wrong (mean batch is 3.7
        messages, not 19). With no instrument on the delivery side the 3x was
        unattributable. Now it is one histogram.

        Never raises: a measurement must not be able to drop an action.
        """
        if self._monitor is None or not action.published_at:
            return
        try:
            self._monitor.histogram(
                "orchestration.hop_seconds",
                max(0.0, time.time() - action.published_at),
                tags={"action": action.action_type},
            )
        except Exception:  # noqa: BLE001 - observability never blocks the run
            _logger.debug("hop metric failed", exc_info=True)

    def _ensure_topic_once(self) -> None:
        if self._topic_ensured:
            return
        self._topic_ensured = True
        if hasattr(self._broker, "ensure_topic"):
            try:
                self._broker.ensure_topic(ORCHESTRATION_TOPIC)
            except Exception:
                _logger.warning("Could not auto-create topic '%s'", ORCHESTRATION_TOPIC)

    async def enqueue(self, action: OrchestrationAction) -> None:
        self.enqueue_nowait(action)

    def enqueue_nowait(self, action: OrchestrationAction) -> None:
        # ``publish_to_topic`` is synchronous, so the async ``enqueue``
        # body has nothing to await — both entry points share it.
        self._ensure_topic_once()
        # Stamped here, not in the caller: this is the last point before the
        # message leaves, so the measurement covers everything the hop costs.
        action.published_at = time.time()
        payload = asdict(action)
        self._broker.publish_to_topic(
            ORCHESTRATION_TOPIC, payload, key=action.run_id,
        )
        _logger.debug("Published %s for run %s to broker", action.action_type, action.run_id)

    async def dequeue(self) -> OrchestrationAction:
        return await self._internal.get()


def _deserialize(data: dict[str, Any]) -> OrchestrationAction | None:
    action_type = data.get("action_type", "")
    cls = _ACTION_CLASSES.get(action_type)
    if cls is None:
        return None
    try:
        # Drop unknown keys rather than letting ``cls(**fields)`` raise. It
        # used to raise, get caught below, and return None — which DROPS the
        # action and hangs the run, silently. That made adding any field to an
        # action a rollback hazard: a pod on older code receiving a newer
        # message would lose it. Forward compatibility has to be the default
        # here, not something the next person remembers.
        known = {f.name for f in dataclasses.fields(cls)}
        fields = {k: v for k, v in data.items()
                  if k != "action_type" and k in known}
        if cls is EditNodeDataAction and isinstance(fields.get("command"), dict):
            # ``command`` was ``asdict(EditNodeDataCommand)`` on the wire;
            # rehydrate it so the worker's use case receives a real dataclass.
            from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import (
                EditNodeDataCommand,
            )
            fields["command"] = EditNodeDataCommand(**fields["command"])
        return cls(**fields)
    except TypeError:
        return None
