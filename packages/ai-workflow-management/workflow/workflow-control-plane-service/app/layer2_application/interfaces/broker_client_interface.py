"""Broker client interfaces for messaging backends.

IBrokerClient defines the core publish/subscribe operations shared by
all backends (Kafka, SAP Event Mesh, etc.).

IQueueBrokerClient extends IBrokerClient with queue-based delivery
operations specific to Event Mesh.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable
from collections.abc import Callable


class IBrokerClient(Protocol):
    """Core broker operations shared by all backends."""

    def publish_to_topic(self, topic: str, message: Any, key: str | None = None) -> None: ...

    def subscribe_to_topic(self, topic: str, callback: Callable) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class IQueueBrokerClient(IBrokerClient, Protocol):
    """Extended broker with queue-based delivery (SAP Event Mesh)."""

    def publish_to_queue(self, queue_name: str, message: Any) -> None: ...

    def consume_from_queue(self, queue_name: str) -> dict | None: ...

    def acknowledge(self, queue_name: str, message_id: str) -> None: ...
