"""gRPC servicer for RunEventService — server-streaming run events.

Provides an alternative to Socket.IO for service-to-service event
consumption. Subscribers open a stream and receive all domain events
for a given run_id until the stream is cancelled.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Any

import grpc
from google.protobuf import struct_pb2
from google.protobuf.json_format import ParseDict

logger = logging.getLogger(__name__)

# Inline proto-like message (avoid proto import for a lightweight streaming event).
# The payload is a google.protobuf.Struct so any dict can be sent.


class _RunEventMessage:
    """Lightweight stand-in until a dedicated run_event.proto is added."""

    def __init__(self, event_type: str, payload: dict) -> None:
        self.event_type = event_type
        self.payload = payload


class RunEventSubscriptionManager:
    """Manages per-run subscriber queues.

    The ``RealtimeBroadcastingPublisher`` (or a wrapper) feeds events into
    this manager.  Each active ``StreamRunEvents`` call drains its own queue.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        queues = self._subscribers.get(run_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            self._subscribers.pop(run_id, None)

    def publish(self, run_id: str, event_type: str, payload: dict) -> None:
        for q in self._subscribers.get(run_id, []):
            try:
                q.put_nowait(_RunEventMessage(event_type, payload))
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full for run=%s, dropping event %s", run_id, event_type)


# Global singleton — wired via bootstrap
run_event_manager = RunEventSubscriptionManager()
