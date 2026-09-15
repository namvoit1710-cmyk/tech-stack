"""Outbox repository — Phase 3 Sub 3.2.

Thin facade the ``OutboxDispatcher`` uses to enumerate unresolved
``OutboxRequested`` entries and track per-pod retry attempts. Storage
piggybacks on ``IEventLogRepository``: ``OutboxRequested`` and
``OutboxResolved`` are ordinary log envelopes, so the repository
materialises "unresolved" by pairing them up. No new persistence table
is required.

Attempt tracking (``record_attempt`` / ``get_attempts``) is in-memory
per pod. A pod restart resets the counter; the next pod inherits the
unresolved entry and gets its own retry budget. Acceptable because
the downstream call uses an idempotency key — duplicate attempts are
safe.

See ``docs/design/phase-3-outbox-pattern.md`` §5 for dispatcher
semantics and ``docs/design/phase-3-executor-extraction.md`` §7 for
the broader Sub 3.2 scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class OutboxEntry:
    """An unresolved outbox request.

    Returned from ``IOutboxRepository.list_unresolved``. The dispatcher
    feeds ``payload`` and ``idempotency_key`` to the kind-specific
    handler; ``sequence`` lets callers paginate via the
    ``since_sequence`` parameter.
    """

    outbox_id: str
    run_id: str
    workflow_id: str
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    sequence: int = 0
    requested_at: str = ""
    causation_id: str = ""


class IOutboxRepository(Protocol):
    """Read + retry-tracking facade over outbox events in the event log."""

    def list_unresolved(
        self, since_sequence: int = 0, limit: int = 100,
    ) -> list[OutboxEntry]:
        """Return outbox entries that have ``OutboxRequested`` but no
        matching ``OutboxResolved``, in append order.

        ``since_sequence`` filters to entries whose ``OutboxRequested``
        envelope sequence is strictly greater than the watermark — the
        dispatcher uses this to avoid re-scanning resolved entries on
        every poll. ``limit`` caps the batch size; the dispatcher
        retries on the next poll if more entries exist.
        """

    def record_attempt(self, outbox_id: str) -> int:
        """Increment the in-memory attempt counter for ``outbox_id``
        and return the new value.

        Counters reset on pod restart — see module docstring.
        """

    def get_attempts(self, outbox_id: str) -> int:
        """Return the current attempt count for ``outbox_id``, or 0
        if the dispatcher has not attempted it yet on this pod.
        """

    def reset_attempts(self, outbox_id: str) -> None:
        """Drop the attempt counter for ``outbox_id`` — called after
        an entry is resolved (ok or failed) to release memory.
        """
