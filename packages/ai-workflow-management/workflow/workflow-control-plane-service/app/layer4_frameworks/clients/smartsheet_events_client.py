"""Smartsheet Event Reporting HTTP client (smartsheet-event-trigger S2).

Thin transport over ``GET /2.0/events`` — the one place that knows the Smartsheet
wire format. Provides ``fetch_page`` for the pure drain loop
(:func:`app.layer2_application.services.smartsheet_poller.drain.drain_events`).
Follows the house style (``httpx.Client``, Bearer auth) used by the other layer4
clients.

⚠️TO-VERIFY against a live 200 (all probes so far were 403 — no sysadmin token):
the response field names (``data`` / ``nextStreamPosition`` / ``moreAvailable``)
and the ``maxCount`` cap. If they differ, this module + the page mapping are the
only things that change.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.layer2_application.services.smartsheet_poller.drain import SmartsheetEventsPage

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.smartsheet.com/2.0"
_DEFAULT_TIMEOUT = httpx.Timeout(30.0)
# Smartsheet maxCount cap — docs say up to 10000. ⚠️TO-VERIFY with a live token.
_DEFAULT_MAX_COUNT = 10000


class SmartsheetApiError(Exception):
    """Any non-success from the Smartsheet API."""


class SmartsheetAuthError(SmartsheetApiError):
    """401/403 — bad token, not a System Admin, or plan lacks Event Reporting (1013)."""


class SmartsheetRateLimitError(SmartsheetApiError):
    """429 — caller should back off and retry the same cursor next cycle."""


class SmartsheetEventsClient:
    """Fetches one page of ``/2.0/events`` per call; the drain loop paginates."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        max_count: int = _DEFAULT_MAX_COUNT,
        client: httpx.Client | None = None,
        timeout: httpx.Timeout = _DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._base = base_url.rstrip("/")
        self._max_count = max_count
        # Injectable for tests (httpx.MockTransport); owns a real Client otherwise.
        self._client = client or httpx.Client(timeout=timeout)

    def fetch_page(self, params: dict[str, Any]) -> SmartsheetEventsPage:
        """GET one events page. ``params`` carries ``since`` OR ``streamPosition``."""
        query: dict[str, Any] = {"maxCount": self._max_count, **params}
        resp = self._client.get(
            f"{self._base}/events",
            params=query,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if resp.status_code in (401, 403):
            raise SmartsheetAuthError(
                f"Smartsheet events auth failed ({resp.status_code}): {resp.text[:300]}"
            )
        if resp.status_code == 429:
            raise SmartsheetRateLimitError("Smartsheet events rate-limited (429)")
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SmartsheetApiError(
                f"Smartsheet events error ({resp.status_code}): {resp.text[:300]}"
            ) from exc

        body = resp.json()
        return SmartsheetEventsPage(
            events=list(body.get("data", []) or []),
            next_stream_position=str(body.get("nextStreamPosition", "") or ""),
            more_available=bool(body.get("moreAvailable", False)),
        )
