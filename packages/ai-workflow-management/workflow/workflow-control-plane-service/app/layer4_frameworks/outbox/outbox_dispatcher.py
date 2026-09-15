"""Outbox dispatcher — Phase 3 Sub 3.2.

Polls ``IOutboxRepository.list_unresolved`` on a loop, dispatches each
entry to its kind-specific handler, and appends ``OutboxResolved`` to
the event log with the outcome. One dispatcher per control-plane pod.

Retry semantics (per ``docs/design/phase-3-outbox-pattern.md`` §5):
  - ``RetryableError`` → increment per-entry attempt counter, sleep
    with exponential backoff (with 20% jitter), poll again.
  - ``PermanentError`` → emit ``OutboxResolved(outcome="failed")``
    immediately; entry leaves the dispatcher's view.
  - ``MAX_ATTEMPTS`` exceeded → same terminal failure path.

Poll cadence:
  - 100 ms when entries existed in the last batch (catch-up mode).
  - ``poll_interval_s`` (default 1.0 s) when the last batch was empty.

Sub 3.2 ships this with no producer wired — under
``OUTBOX_DATA_EDIT=off`` and ``OUTBOX_HITL_RESUME=off`` no handler
emits ``OutboxRequested``, the polled list stays empty, and the
dispatcher idles harmlessly.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Any, Protocol

from app.layer1_domain.events import EventTopic, OutboxResolved
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.interfaces.event_log_repository_interface import (
    EventLogEnvelope,
    IEventLogRepository,
)
from app.layer2_application.interfaces.outbox_repository_interface import (
    IOutboxRepository,
    OutboxEntry,
)
from app.layer4_frameworks.outbox.outbox_errors import (
    PermanentError,
    RetryableError,
)


_logger = logging.getLogger(__name__)


class IOutboxHandler(Protocol):
    async def perform(self, entry: OutboxEntry) -> dict[str, Any]: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff with 20% jitter — caps at ~30 s."""
    base = min(30.0, (2.0 ** max(1, attempt)) * 0.1)
    jitter = base * 0.2 * random.random()
    return base + jitter


class OutboxDispatcher:
    """Async loop that drains the outbox.

    The dispatcher is created at startup (``bootstrap.py``), wired
    into the FastAPI lifespan as a background task, and cancelled on
    shutdown. It is safe to construct with an empty handler map — kind
    values not in the map raise ``PermanentError`` so the entry is
    quickly retired.
    """

    BATCH_LIMIT = 100

    def __init__(
        self,
        outbox_repo: IOutboxRepository,
        event_log_repo: IEventLogRepository,
        handlers: dict[str, IOutboxHandler],
        *,
        poll_interval_s: float = 1.0,
        max_attempts: int = 5,
        clock: Any = None,
        awaiter: Any = None,
    ) -> None:
        self._outbox_repo = outbox_repo
        self._event_log_repo = event_log_repo
        self._handlers = handlers
        self._poll_interval_s = poll_interval_s
        self._max_attempts = max_attempts
        self._clock = clock
        self._awaiter = awaiter
        self._stopped = asyncio.Event()

    async def run_forever(self) -> None:
        """Main loop — runs until ``stop`` is called."""
        while not self._stopped.is_set():
            try:
                drained = await self.drain_once()
            except Exception:  # pragma: no cover — defensive
                _logger.exception("outbox dispatcher iteration failed")
                drained = 0
            sleep_s = 0.1 if drained > 0 else self._poll_interval_s
            try:
                await asyncio.wait_for(
                    self._stopped.wait(), timeout=sleep_s,
                )
            except asyncio.TimeoutError:
                pass

    async def drain_once(self) -> int:
        """Process one batch of unresolved entries.

        Returns the number of entries the dispatcher successfully
        retired (resolved ok or failed). Tests call this directly to
        avoid the run_forever loop.
        """
        entries = self._outbox_repo.list_unresolved(limit=self.BATCH_LIMIT)
        retired = 0
        for entry in entries:
            handler = self._handlers.get(entry.kind)
            if handler is None:
                self._record_failed(
                    entry,
                    f"no handler registered for kind={entry.kind!r}",
                )
                retired += 1
                continue
            try:
                result = await handler.perform(entry)
            except PermanentError as exc:
                self._record_failed(entry, str(exc))
                retired += 1
                continue
            except RetryableError as exc:
                attempts = self._outbox_repo.record_attempt(entry.outbox_id)
                if attempts >= self._max_attempts:
                    self._record_failed(
                        entry,
                        f"max attempts ({self._max_attempts}) exceeded: {exc}",
                    )
                    retired += 1
                    continue
                # Sleep before the next iteration to give upstream a
                # chance to recover. Capped — see ``_backoff_seconds``.
                await asyncio.sleep(_backoff_seconds(attempts))
                continue
            except Exception as exc:  # noqa: BLE001 — catch-all logged
                _logger.exception(
                    "outbox handler %s raised an unclassified exception",
                    entry.kind,
                )
                self._record_failed(entry, f"unhandled: {exc}")
                retired += 1
                continue
            self._record_ok(entry, result)
            retired += 1
        return retired

    def stop(self) -> None:
        """Signal ``run_forever`` to exit at its next iteration."""
        self._stopped.set()

    # ─────────────── internal: write OutboxResolved ────────────────

    def _record_ok(self, entry: OutboxEntry, result: dict[str, Any]) -> None:
        self._append_resolved(
            entry, outcome="ok", result=result, error=None,
        )
        self._outbox_repo.reset_attempts(entry.outbox_id)

    def _record_failed(self, entry: OutboxEntry, error: str) -> None:
        self._append_resolved(
            entry, outcome="failed", result={}, error=error,
        )
        self._outbox_repo.reset_attempts(entry.outbox_id)

    def _append_resolved(
        self,
        entry: OutboxEntry,
        *,
        outcome: str,
        result: dict[str, Any],
        error: str | None,
    ) -> None:
        # ``deterministic_event_id`` makes the resolved event id stable
        # across replays — the log dedupe collapses re-runs of the
        # dispatcher to the same envelope.
        event_id = EventId.derive(
            entry.outbox_id, f"resolved:{outcome}",
        )
        resolved = OutboxResolved(
            event_id=event_id,
            outbox_id=entry.outbox_id,
            run_id=entry.run_id,
            workflow_id=entry.workflow_id,
            outcome=outcome,
            result=result,
            error=error,
        )
        envelope = EventLogEnvelope(
            event_id=event_id,
            run_id=entry.run_id,
            topic=str(EventTopic.OUTBOX_RESOLVED),
            event_type="OutboxResolved",
            timestamp=_now_iso(),
            payload={
                "outbox_id": resolved.outbox_id,
                "run_id": resolved.run_id,
                "workflow_id": resolved.workflow_id,
                "outcome": resolved.outcome,
                "result": resolved.result,
                "error": resolved.error,
            },
            causation_id=entry.causation_id or entry.outbox_id,
        )
        self._event_log_repo.append(envelope)
        # Single-pod tee: notify in-process awaiters so an HTTP caller
        # blocking on ``OutboxAwaiter.wait`` releases without waiting
        # for a projector cache refresh. Cross-instance delivery is
        # out of scope (sticky routing keeps producer + dispatcher on
        # the same pod under M.1).
        if self._awaiter is not None:
            try:
                self._awaiter.deliver(
                    entry.outbox_id,
                    outcome=outcome,
                    result=result,
                    error=error,
                )
            except Exception:  # pragma: no cover — never break write path
                _logger.exception(
                    "outbox awaiter.deliver failed for %s", entry.outbox_id,
                )
