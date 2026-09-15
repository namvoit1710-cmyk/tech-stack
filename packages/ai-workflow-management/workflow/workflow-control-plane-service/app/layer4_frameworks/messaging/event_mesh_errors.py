"""Custom exception types for Event Mesh broker client."""

from __future__ import annotations


class EventMeshError(Exception):
    """Base class for Event Mesh failures."""


class EventMeshPayloadTooLarge(EventMeshError):
    """Raised when the serialized payload exceeds the configured size limit.

    The size limit exists to stop oversized events from reaching Event Mesh
    (which has a ~1MB payload cap) and returning confusing 413 errors.
    """

    def __init__(self, payload_size: int, limit: int) -> None:
        self.payload_size = payload_size
        self.limit = limit
        super().__init__(
            f"Event Mesh payload {payload_size} bytes exceeds limit {limit} bytes"
        )


class EventMeshPublishFailed(EventMeshError):
    """Raised when a publish exhausts all retry attempts."""

    def __init__(self, topic: str, attempts: int, last_error: str) -> None:
        self.topic = topic
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Event Mesh publish to {topic!r} failed after {attempts} attempt(s): {last_error}"
        )


class EventMeshCircuitOpen(EventMeshError):
    """Raised when the circuit breaker is open (Event Mesh considered unhealthy).

    Reserved for Phase 3 — defined now so callers can catch it once enabled.
    """


class EventMeshBufferFull(EventMeshError):
    """Raised when the outbound send buffer is full under the "raise" policy.

    Reserved for Phase 2 — defined now so callers can catch it once enabled.
    """
