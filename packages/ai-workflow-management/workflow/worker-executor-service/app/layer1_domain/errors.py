"""Domain errors shared across the executor's layers.

Lives in layer1 so a layer4 broker client may *raise* it and a layer2 use case
may *catch* it without either importing the other (the import-linter contract
forbids layer2 → layer4).
"""

from __future__ import annotations


class MessageTooLargeError(Exception):
    """A message exceeds the broker's hard payload cap and cannot be published.

    Raised *before* the network call: SAP Event Mesh answers an oversized POST
    with a 413, and Kafka rejects it at the producer. Publishing it anyway only
    converts a knowable, local failure into a remote one — and, on the result
    path, into a silently lost task result (SA-1951).

    The caller is expected to catch this and degrade to a small message (e.g. a
    pointer, or a ``task.failed``) rather than let the result vanish.
    """

    def __init__(self, destination: str, size_bytes: int, max_bytes: int) -> None:
        self.destination = destination
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"message for {destination!r} is {size_bytes} bytes, over the "
            f"{max_bytes}-byte broker cap — almost always a large inline worker "
            f"output; relay a pointer (worker-side streaming, SA-1944) instead"
        )


class InstancePinUnavailableError(Exception):
    """A task pinned to a SPECIFIC worker instance (SA-2424 debug routing) cannot
    be dispatched to that instance.

    Raised by the dispatcher (layer4) when the pinned instance is unknown,
    unhealthy, of the wrong worker_type, or has no reachable endpoint. Caught by
    ExecuteTaskUseCase (layer2), which FAILS the task fast rather than silently
    falling back to another instance — a silent fallback would defeat the whole
    point of a debug pin (the user asked to run on THIS instance and nowhere
    else). It is a non-retryable, deterministic failure: the operator picked an
    instance that is no longer eligible, so retrying the same pin only fails again.
    """

    def __init__(self, target_instance_id: str, worker_type: str, reason: str) -> None:
        self.target_instance_id = target_instance_id
        self.worker_type = worker_type
        self.reason = reason
        super().__init__(
            f"pinned worker instance {target_instance_id!r} for worker_type "
            f"{worker_type!r} cannot be used: {reason}"
        )


class ResultUndeliverableError(Exception):
    """A worker result could not be handed to the control plane at all.

    Raised only when BOTH the result publish and the small ``task.failed``
    fallback failed — i.e. the broker itself is unreachable. The pending entry is
    put back before this is raised, so the sweeper can still time the task out;
    surfacing it (rather than swallowing it) also lets the worker retry the
    callback, which is the right move for a transient broker outage.
    """

