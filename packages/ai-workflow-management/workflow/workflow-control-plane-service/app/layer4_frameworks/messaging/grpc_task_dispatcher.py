"""gRPC task dispatcher — sends node.dispatched events to the Executor via gRPC.

Drop-in replacement for ``HttpTaskDispatcher``. Same decorator pattern:
wraps an inner event publisher and intercepts ``node.dispatched`` topics.
"""
from __future__ import annotations

import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import grpc
import httpx
from google.protobuf import struct_pb2

from workflow_proto.v1 import task_dispatch_pb2 as pb
from workflow_proto.v1 import task_dispatch_pb2_grpc as pb_grpc

from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher

logger = logging.getLogger(__name__)

_DISPATCH_TOPICS = frozenset({"task.dispatch", "node.dispatched", "taskdispatched"})
_DEFAULT_MAX_WORKERS = 10


def _dict_to_struct(d: dict) -> struct_pb2.Struct:
    s = struct_pb2.Struct()
    if d:
        s.update(d)
    return s


class GrpcTaskDispatcher(IEventPublisher):
    """``IEventPublisher`` that dispatches tasks to the Executor via gRPC."""

    def __init__(
        self,
        fallback_publisher: IEventPublisher,
        grpc_target: str,
        max_workers: int = _DEFAULT_MAX_WORKERS,
        http_fallback_url: Optional[str] = None,
    ) -> None:
        self._fallback = fallback_publisher
        self._target = grpc_target
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="grpc-task-dispatch",
        )
        self._channel = grpc.insecure_channel(self._target)
        self._stub = pb_grpc.TaskDispatchServiceStub(self._channel)
        self._http_fallback_url = (
            http_fallback_url.rstrip("/") if http_fallback_url else None
        )
        self._http_client = (
            httpx.Client(
                timeout=120.0,
                limits=httpx.Limits(
                    max_connections=max_workers,
                    max_keepalive_connections=max_workers,
                ),
            )
            if self._http_fallback_url
            else None
        )

    def publish(self, topic: str, event: Any) -> None:
        if self._fallback:
            self._fallback.publish(topic, event)

        if topic in _DISPATCH_TOPICS and self._should_dispatch_to_worker(event):
            payload = self._to_payload(event)
            worker_type = (payload or {}).get("worker_type") or ""
            # ``builtin:*`` worker_types are handled inline by the engine
            # (BUILTIN + PENDING node types). Their ``node.dispatched``
            # is a tracking signal only — forwarding would phantom-
            # complete the task. See ``HttpTaskDispatcher.publish``.
            if payload and worker_type and not worker_type.startswith("builtin:"):
                self._executor.submit(self._dispatch_grpc, payload)

    @staticmethod
    def _should_dispatch_to_worker(event: Any) -> bool:
        """Lifecycle-only ``NodeDispatched`` (``dispatch_to_worker=False``,
        e.g. TaskCreatorConsumer's) must reach the projector but never the
        worker. Mirrors ``HttpTaskDispatcher._should_dispatch_to_worker``.

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
    def _to_payload(event: Any) -> dict | None:
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

    def _dispatch_grpc(self, payload: dict) -> None:
        try:
            # SA-1490 Phase 2: ``spec_version`` is in ``payload`` but the proto
            # ``DispatchTaskRequest`` has no field for it yet, so the gRPC-native
            # transport cannot carry it (the HTTP fallback below posts the raw
            # ``payload`` dict, which does). Follow-up: add ``spec_version`` as
            # field 8 in task_dispatch.proto + regenerate, then set it here.
            request = pb.DispatchTaskRequest(
                task_id=payload.get("task_id", ""),
                run_id=payload.get("run_id", ""),
                node_id=payload.get("node_id", ""),
                worker_type=payload.get("worker_type", ""),
                input=_dict_to_struct(payload.get("input", {})),
                node_config=_dict_to_struct(payload.get("node_config", {})),
                idempotency_key=payload.get("idempotency_key", ""),
            )
            response = self._stub.DispatchTask(request, timeout=120.0)
            logger.info(
                "gRPC dispatched task %s -> status=%s",
                payload.get("task_id", "?"),
                response.status,
            )
        except Exception as exc:
            logger.error("gRPC dispatch failed for task %s: %s", payload.get("task_id", "?"), exc)
            if self._http_client is not None:
                self._dispatch_http_fallback(payload)

    def _dispatch_http_fallback(self, payload: dict) -> None:
        url = f"{self._http_fallback_url}/api/v1/tasks/execute"
        try:
            response = self._http_client.post(url, json=payload)
            response.raise_for_status()
            logger.info(
                "HTTP fallback dispatched task %s -> %s",
                payload.get("task_id", "?"),
                response.status_code,
            )
        except Exception as exc:
            logger.error(
                "HTTP fallback dispatch failed for task %s: %s",
                payload.get("task_id", "?"), exc,
            )

    def close(self) -> None:
        """Shutdown executor and close gRPC channel."""
        self._executor.shutdown(wait=False)
        self._channel.close()
        if self._http_client is not None:
            self._http_client.close()
