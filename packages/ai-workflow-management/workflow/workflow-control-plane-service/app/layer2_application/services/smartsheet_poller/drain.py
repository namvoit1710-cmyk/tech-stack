"""Smartsheet Event Reporting drain loop (smartsheet-event-trigger S2).

``GET /2.0/events`` returns one page of events plus a ``nextStreamPosition`` token
and a ``moreAvailable`` flag. To pull everything since the last run you page:
start from a persisted ``stream_position`` (resume) or a ``since`` timestamp
(bootstrap), then keep requesting with the returned ``nextStreamPosition`` until
``moreAvailable`` is false.

This module is the pure orchestration — it takes a ``fetch_page`` callable and
knows nothing about HTTP, so it is fully unit-testable with a fake fetcher (no
live token). The HTTP transport lives in the layer4 ``SmartsheetEventsClient``,
which returns the :class:`SmartsheetEventsPage` defined here (layer4 → layer2 is
the allowed dependency direction; this module never imports the client).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

# Backstop so a misbehaving stream (moreAvailable never clears) can't spin
# forever in one cycle; the cursor still advances, so the rest drains next cycle.
_DEFAULT_MAX_PAGES = 1000


@dataclass
class SmartsheetEventsPage:
    """One ``GET /2.0/events`` response page (⚠️TO-VERIFY field names vs a live 200)."""

    events: list[dict] = field(default_factory=list)
    next_stream_position: str = ""
    more_available: bool = False


@dataclass
class DrainResult:
    events: list[dict]
    # The final ``nextStreamPosition`` to persist so the next cycle resumes here.
    stream_position: str
    pages: int
    # True if we stopped at _max_pages with more still available (deferred, not lost).
    truncated: bool


FetchPage = Callable[[dict[str, Any]], SmartsheetEventsPage]


def drain_events(
    fetch_page: FetchPage,
    *,
    since: str | None = None,
    stream_position: str | None = None,
    max_pages: int = _DEFAULT_MAX_PAGES,
) -> DrainResult:
    """Drain events from ``stream_position`` (resume) or ``since`` (bootstrap).

    Pages until ``moreAvailable`` is false (or the ``max_pages`` backstop). Returns
    every event pulled plus the final stream position to persist. ``stream_position``
    takes precedence over ``since`` when both are given (resume beats re-bootstrap).
    """
    if not stream_position and not since:
        raise ValueError("drain_events needs either since or stream_position")

    params: dict[str, Any] = (
        {"streamPosition": stream_position} if stream_position else {"since": since}
    )
    events: list[dict] = []
    pos = stream_position or ""
    pages = 0
    truncated = False

    while True:
        page = fetch_page(params)
        pages += 1
        events.extend(page.events)
        if page.next_stream_position:
            pos = page.next_stream_position

        if not page.more_available:
            break
        if not page.next_stream_position:
            # Defensive: more claimed but no cursor to advance — stop rather than
            # refetch the same page forever. Next cycle retries from `pos`.
            logger.warning(
                "Smartsheet drain: moreAvailable=true but empty nextStreamPosition; stopping"
            )
            break
        if pages >= max_pages:
            truncated = True
            logger.warning(
                "Smartsheet drain hit max_pages=%d with more available; "
                "%d events so far, deferring the rest to the next cycle (cursor advanced)",
                max_pages, len(events),
            )
            break
        params = {"streamPosition": page.next_stream_position}

    return DrainResult(events=events, stream_position=pos, pages=pages, truncated=truncated)
