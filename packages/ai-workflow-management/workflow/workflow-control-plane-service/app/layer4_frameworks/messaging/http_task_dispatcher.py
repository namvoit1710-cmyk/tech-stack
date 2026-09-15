"""HTTP task dispatcher — sends node.dispatched events to Worker Execution via HTTP.

Wraps an existing event publisher. When a node.dispatched event arrives, it fires
an HTTP POST to the Worker Execution Service using a bounded thread pool.
"""
import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher

logger = logging.getLogger(__name__)

_DISPATCH_TOPICS = frozenset({"task.dispatch", "node.dispatched", "taskdispatched"})
_DEFAULT_MAX_WORKERS = 10


class HttpTaskDispatcher(IEventPublisher):
    def __init__(
        self,
        fallback_publisher: IEventPublisher,
        worker_execution_url: str,
        max_workers: int = _DEFAULT_MAX_WORKERS,
    ) -> None:
        self._fallback = fallback_publisher
        self._worker_execution_url = worker_execution_url.rstrip("/")
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="http-task-dispatch",
        )
        self._client = httpx.Client(
            timeout=120.0,
            limits=httpx.Limits(
                max_connections=max_workers,
                max_keepalive_connections=max_workers,
            ),
        )

    def publish(self, topic: str, event: Any) -> None:
        if self._fallback:
            self._fallback.publish(topic, event)

        if topic in _DISPATCH_TOPICS and self._should_dispatch_to_worker(event):
            payload = self._to_dispatch_payload(event)
            worker_type = (payload or {}).get("worker_type") or ""
            # ``builtin:*`` worker_types are BUILTIN (CONDITION, SWITCH, …)
            # or PENDING (INPUT, HUMAN_ACTION, …) nodes that the engine
            # handles inline. Their ``node.dispatched`` events are pure
            # tracking signals — forwarding them to the executor would
            # round-trip a phantom completion (e.g. HUMAN_ACTION auto-
            # completing with output_port="success" before the user has
            # responded). Only real worker tasks reach the executor.
            if payload and worker_type and not worker_type.startswith("builtin:"):
                self._executor.submit(self._dispatch_http, payload)

    @staticmethod
    def _should_dispatch_to_worker(event: Any) -> bool:
        """Whether this ``node.dispatched`` event should be POSTed to the worker.

        ``TaskCreatorConsumer`` (executor / shadow mode) emits a
        lifecycle-only ``NodeDispatched`` with ``dispatch_to_worker=False``
        and empty input — it must reach the projector/frontend (via the
        fallback publisher) but NOT the worker, else the worker is dispatched
        with no resolved input and fails (e.g. ``'url' is required``). The
        authoritative dispatch (engine / executor handler path) leaves the
        flag ``True``.

        By the time the event reaches this wrapper it has been serialised to a
        **flat dict** by ``EnrichingEventPublisher`` (``dataclasses.asdict`` →
        inner publisher), so ``dispatch_to_worker`` is a TOP-LEVEL key — not
        nested under ``payload``. Reading the wrong level here let the
        ``False`` tracking event through to the worker (the ``'url' is
        required`` flake). Check the top level first, fall back to a
        ``payload``-wrapped envelope, and default ``True`` when absent.
        """
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            return getattr(event, "dispatch_to_worker", True)
        if isinstance(event, dict):
            if "dispatch_to_worker" in event:
                return bool(event["dispatch_to_worker"])
            return event.get("payload", {}).get("dispatch_to_worker", True)
        return True

    @staticmethod
    def _to_dispatch_payload(event: Any) -> dict | None:
        """Extract the HTTP POST payload from a dataclass or dict event."""
        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            return {
                "task_id": getattr(event, "task_id", ""),
                "run_id": getattr(event, "run_id", ""),
                "node_id": getattr(event, "node_id", ""),
                "worker_type": getattr(event, "worker_type", ""),
                "spec_version": getattr(event, "spec_version", ""),  # SA-1490 Phase 2
                "target_instance_id": getattr(event, "target_instance_id", ""),  # SA-2424
                "target_host": getattr(event, "target_host", ""),  # SA-2437
                "input": getattr(event, "input", {}),
                "node_config": getattr(event, "node_config", {}),
                "idempotency_key": getattr(event, "idempotency_key", ""),
                "metadata": getattr(event, "metadata", {}) or {},
            }
        if isinstance(event, dict):
            return {
                "task_id": event.get("task_id", ""),
                "run_id": event.get("run_id", ""),
                "node_id": event.get("node_id", ""),
                "worker_type": event.get("worker_type", ""),
                "spec_version": event.get("spec_version", ""),  # SA-1490 Phase 2
                "target_instance_id": event.get("target_instance_id", ""),  # SA-2424
                "target_host": event.get("target_host", ""),  # SA-2437
                "input": event.get("input", {}),
                "node_config": event.get("node_config", {}),
                "idempotency_key": event.get("idempotency_key", ""),
            }
        return None

    def _dispatch_http(self, payload: dict) -> None:
        url = f"{self._worker_execution_url}/api/v1/tasks/execute"
        try:
            response = self._client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Dispatched task %s -> %s", payload.get('task_id', '?'), response.status_code)
        except Exception as exc:
            logger.error("Failed to dispatch task %s: %s", payload.get('task_id', '?'), exc)

    def close(self) -> None:
        """Shutdown executor and close HTTP client."""
        self._executor.shutdown(wait=False)
        self._client.close()
