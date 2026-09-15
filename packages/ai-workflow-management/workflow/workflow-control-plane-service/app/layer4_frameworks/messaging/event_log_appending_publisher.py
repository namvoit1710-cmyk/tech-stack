"""Write-ahead event log decorator — Phase 4b.1.

Outermost publisher in the chain. Every call to ``publish(topic, event)``
first materialises an ``EventLogEnvelope`` and appends it to the
``IEventLogRepository``, then delegates to the inner publisher. The
inner chain stays exactly as it is today — realtime broadcasting,
centralized enrichment, Kafka / Event Mesh / Console — so this layer
cannot introduce any behavior change on the broadcast side.

Why write-ahead:
    If downstream publishing fails (broker outage, enricher bug), the
    event log still holds an authoritative record. Later phases'
    Projector can rebuild from the log alone. Reversing this order
    would leave "ghost" broadcasts that never made it to the log,
    which is the opposite of the property we want for Phase 4b.3.

Failure policy:
    A log-append failure MUST NOT block publish. This is a shadow log
    in Phase 4b.1 — nothing reads from it in production paths yet, and
    blocking publish on log write would create a new availability gap
    for zero current benefit. Exceptions are caught and logged at
    ``error`` level only. When the Projector becomes authoritative in
    Phase 4b.3 the policy flips to "log or fail".

Envelope shape:
    ``run_id`` is extracted with a fallback chain — ``event.run_id``,
    then ``event.parent_run_id``, then ``event.root_run_id``. The
    different ``DomainEvent`` subclasses in ``events.py`` set different
    subsets of these fields; the fallback catches all of them.
    ``event_id`` is the event's own id if present (today random
    UUID4 from ``DomainEvent``'s default factory; Phase 4b.2 will make
    it deterministic for Dispatcher-emitted events).
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
    IEventLogRepository,
)
from app.layer2_application.interfaces.event_publisher_interface import IEventPublisher
from app.layer2_application.interfaces.logger_interface import ILogger


class EventLogAppendingPublisher(IEventPublisher):
    """Publisher decorator that writes every event to the event log first."""

    def __init__(
        self,
        inner: IEventPublisher,
        event_log_repo: IEventLogRepository,
        logger: ILogger | None = None,
    ) -> None:
        self._inner = inner
        self._event_log_repo = event_log_repo
        self._logger = logger

    def publish(self, topic: str, event: Any) -> None:
        try:
            envelope = self._build_envelope(topic, event)
            self._event_log_repo.append(envelope)
        except Exception as exc:  # pragma: no cover — defensive
            if self._logger:
                self._logger.error(
                    "EventLogAppendingPublisher: failed to append event to log (%s): %s",
                    topic, exc,
                )
        self._inner.publish(topic, event)

    # ─────────────────────────── internal ────────────────────────────

    @staticmethod
    def _lookup(event: Any, key: str) -> str:
        """Read ``key`` from a dataclass (attr) or a dict (item) or ''."""
        if isinstance(event, dict):
            return str(event.get(key, "") or "")
        return str(getattr(event, key, "") or "")

    @classmethod
    def _build_envelope(cls, topic: str, event: Any) -> EventLogEnvelope:
        event_id = cls._lookup(event, "event_id") or str(uuid.uuid4())
        run_id = (
            cls._lookup(event, "run_id")
            or cls._lookup(event, "parent_run_id")
            or cls._lookup(event, "root_run_id")
        )
        event_type = type(event).__name__ if not isinstance(event, dict) else (
            str(event.get("_event") or event.get("event_type") or "dict")
        )
        timestamp = cls._lookup(event, "timestamp") or datetime.now(timezone.utc).isoformat()
        source = cls._lookup(event, "source")

        if dataclasses.is_dataclass(event) and not isinstance(event, type):
            payload = dataclasses.asdict(event)
        elif isinstance(event, dict):
            payload = dict(event)
        else:
            payload = {"data": repr(event)}

        return EventLogEnvelope(
            event_id=event_id,
            run_id=run_id,
            topic=topic,
            event_type=event_type,
            timestamp=timestamp,
            payload=payload,
            sequence=0,  # assigned by the repo
            source=source,
            causation_id="",  # populated by Phase 4b.2 Dispatcher
        )
