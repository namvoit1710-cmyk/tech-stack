"""Event Mesh task dispatcher — publishes task-dispatch events to the
``workflow.tasks`` topic so the Worker Execution Service can consume them.

Wraps an existing event publisher. When a dispatchable ``node.dispatched``
event arrives, it publishes the dispatch payload to the broker instead of
calling the executor over HTTP/gRPC. All other events are delegated to the
wrapped publisher.

SA-1526: brought to parity with ``GrpcTaskDispatcher`` / ``HttpTaskDispatcher``
— the first live event_mesh run exposed three latent gaps that meant NO task
ever reached ``workflow.tasks``:

* the engine emits ``node.dispatched`` (not ``task.dispatched``), so the old
  topic set never matched;
* dict events arrive **flat** from ``EnrichingEventPublisher`` (no ``payload``
  envelope), so the old ``event["payload"]`` extraction produced ``{}``;
* lifecycle-only dispatches (``dispatch_to_worker=False``) and inline
  ``builtin:*`` worker types must NOT be forwarded — the sibling dispatchers
  filter them, this one didn't.
"""
import dataclasses
import logging
from typing import Any

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher

logger = logging.getLogger(__name__)

_DISPATCH_TOPICS = frozenset(
    {"task.dispatch", "task.dispatched", "node.dispatched", "taskdispatched"}
)
TASKS_TOPIC = "workflow.tasks"


class EventMeshTaskDispatcher(IEventPublisher):
    def __init__(self, fallback_publisher: Any, broker: Any):
        self._fallback = fallback_publisher
        self._broker = broker

    def publish(self, topic: str, event: Any) -> None:
        if self._fallback:
            self._fallback.publish(topic, event)

        if topic in _DISPATCH_TOPICS and self._should_dispatch_to_worker(event):
            payload = self._to_dispatch_payload(event)
            worker_type = (payload or {}).get("worker_type") or ""
            # ``builtin:*`` worker_types are handled inline by the engine
            # (BUILTIN + PENDING node types). Their ``node.dispatched`` is a
            # tracking signal only — forwarding would phantom-complete the
            # task. Mirrors GrpcTaskDispatcher / HttpTaskDispatcher.
            if payload and worker_type and not worker_type.startswith("builtin:"):
                self._dispatch(payload)

    @staticmethod
    def _should_dispatch_to_worker(event: Any) -> bool:
        """Lifecycle-only ``NodeDispatched`` (``dispatch_to_worker=False``,
        e.g. TaskCreatorConsumer's) must reach the projector but never the
        worker. Mirrors ``GrpcTaskDispatcher._should_dispatch_to_worker``.

        The event arrives as a **flat dict** (``EnrichingEventPublisher`` does
        ``dataclasses.asdict`` and forwards it inward), so the flag is a
        top-level key — not nested under ``payload``. Read the top level first,
        fall back to a ``payload``-wrapped envelope, default ``True``."""
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            return getattr(event, "dispatch_to_worker", True)
        if isinstance(event, dict):
            if "dispatch_to_worker" in event:
                return bool(event["dispatch_to_worker"])
            return event.get("payload", {}).get("dispatch_to_worker", True)
        return True

    @staticmethod
    def _to_dispatch_payload(event: Any) -> dict | None:
        """Extract the dispatch payload — same shape as the gRPC/HTTP
        dispatchers (flat dict keys, ``payload`` envelope as fallback)."""
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            def get(k: str, d: Any = "") -> Any:
                return getattr(event, k, d)
        elif isinstance(event, dict):
            # Flat dict first (the real shape); tolerate a payload envelope.
            source = (
                event
                if "task_id" in event or "worker_type" in event
                else event.get("payload", {}) or {}
            )

            def get(k: str, d: Any = "") -> Any:
                return source.get(k, d)
        else:
            return None
        return {
            "task_id": get("task_id"),
            "run_id": get("run_id"),
            "node_id": get("node_id"),
            "worker_type": get("worker_type"),
            "spec_version": get("spec_version"),  # SA-1490 Phase 2
            "target_instance_id": get("target_instance_id"),  # SA-2424 debug pin
            "target_host": get("target_host"),  # SA-2437 host fallback
            "input": get("input", {}) or {},
            "node_config": get("node_config", {}) or {},
            "idempotency_key": get("idempotency_key"),
        }

    def _dispatch(self, payload: dict) -> None:
        try:
            self._broker.publish_to_topic(
                TASKS_TOPIC, payload, key=payload.get("run_id") or None
            )
            logger.info("Dispatched task %s -> %s", payload.get("task_id", "?"), TASKS_TOPIC)
        except Exception as exc:
            logger.error("Failed to dispatch task %s: %s", payload.get("task_id", "?"), exc)
