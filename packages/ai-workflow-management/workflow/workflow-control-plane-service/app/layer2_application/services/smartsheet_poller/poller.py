"""Smartsheet poller — one drain→publish cycle + the interval loop (smartsheet-event-trigger S4).

Ties S1 (cursor store) and S2 (drain loop + client) together and hands events to the
canonical trigger topic, reusing the SA-2059 consumer downstream. One cycle:

1. read the persisted stream cursor (bootstrap from ``since`` when there is none),
2. drain ``GET /2.0/events`` until ``moreAvailable`` clears,
3. normalise + filter each event to an envelope (``normalize`` returns ``None`` to skip),
4. publish each envelope (physical topic wired by the caller — see bootstrap S5),
5. **persist the final cursor AFTER publishing** — at-least-once: a crash mid-cycle
   re-drains the overlap next time, and the EventTriggerConsumer's ``EventId``-derived
   run id folds the duplicates. The normaliser is what stamps that ``event_id``.

The poller stays agnostic of HTTP, the broker, and normalisation (all injected), so it
is fully unit-testable with fakes and no live token. ``normalize`` is supplied by S3;
``publish`` by bootstrap (S5) as ``lambda env: broker.publish_to_topic(TRIGGERS_TOPIC, env)``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.layer1_domain.entities.smartsheet_stream_cursor import SmartsheetStreamCursor
from app.layer2_application.repositories.smartsheet_cursor_repository_interface import (
    ISmartsheetCursorRepository,
)
from app.layer2_application.services.smartsheet_poller.drain import (
    FetchPage,
    drain_events,
)

logger = logging.getLogger(__name__)

# event dict -> canonical envelope, or None to skip (filtered / unmapped object-action).
Normalizer = Callable[[dict], dict | None]
# envelope -> published (caller pins the physical topic to the canonical inbox).
Publish = Callable[[dict], None]

_DEFAULT_MAX_PAGES = 1000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PollCycleResult:
    drained: int
    published: int
    # normalize returned None (filtered/unmapped) or raised (poison — logged, skipped).
    skipped: int
    truncated: bool
    stream_position: str


class SmartsheetPoller:
    def __init__(
        self,
        *,
        cursor_repo: ISmartsheetCursorRepository,
        fetch_page: FetchPage,
        normalize: Normalizer,
        publish: Publish,
        bootstrap_since: Callable[[], str],
        cursor_id: str = "smartsheet",
        max_pages: int = _DEFAULT_MAX_PAGES,
        clock: Callable[[], str] = _now_iso,
    ) -> None:
        self._cursor_repo = cursor_repo
        self._fetch_page = fetch_page
        self._normalize = normalize
        self._publish = publish
        self._bootstrap_since = bootstrap_since
        self._cursor_id = cursor_id
        self._max_pages = max_pages
        self._clock = clock

    def run_cycle(self) -> PollCycleResult:
        """Drain, publish, then persist the cursor. Raises on transport/broker
        failure (cursor stays put → the interval loop retries and dedup covers it)."""
        cursor = self._cursor_repo.get(self._cursor_id)
        position = cursor.stream_position if cursor else ""

        if position:
            result = drain_events(
                self._fetch_page, stream_position=position, max_pages=self._max_pages
            )
        else:
            # First run for this cursor: bootstrap from a `since` timestamp.
            result = drain_events(
                self._fetch_page, since=self._bootstrap_since(), max_pages=self._max_pages
            )

        published = 0
        skipped = 0
        for event in result.events:
            try:
                envelope = self._normalize(event)
            except Exception:
                # A single malformed event must not poison the whole stream.
                logger.exception("Smartsheet normalize failed; skipping one event")
                skipped += 1
                continue
            if envelope is None:
                skipped += 1
                continue
            # A publish failure propagates: cursor is NOT advanced below, so the
            # next cycle re-drains from here and dedup folds anything already sent.
            self._publish(envelope)
            published += 1

        self._cursor_repo.save(
            SmartsheetStreamCursor(
                id=self._cursor_id,
                stream_position=result.stream_position,
                updated_at=self._clock(),
            )
        )

        if result.truncated:
            logger.info(
                "Smartsheet cycle truncated at max_pages; remainder drains next cycle "
                "(cursor advanced to %r)", result.stream_position,
            )
        return PollCycleResult(
            drained=len(result.events), published=published, skipped=skipped,
            truncated=result.truncated, stream_position=result.stream_position,
        )

    async def run_forever(
        self,
        interval_seconds: float,
        *,
        should_continue: Callable[[], bool] | None = None,
    ) -> None:
        """Run a cycle every ``interval_seconds``. Swallows per-cycle errors (auth,
        rate-limit, broker) and retries next tick so one bad cycle never kills the
        loop. ``should_continue`` bounds the loop in tests; None = run until cancelled."""
        while should_continue is None or should_continue():
            try:
                # Offload the sync cycle so the httpx/DB calls don't block the loop.
                await asyncio.to_thread(self.run_cycle)
            except Exception:
                logger.exception("Smartsheet poll cycle failed; retrying next interval")
            await asyncio.sleep(interval_seconds)
