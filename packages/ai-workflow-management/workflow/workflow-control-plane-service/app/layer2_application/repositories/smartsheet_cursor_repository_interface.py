"""Smartsheet stream-cursor repository interface (smartsheet-event-trigger S1)."""

from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.smartsheet_stream_cursor import SmartsheetStreamCursor


class ISmartsheetCursorRepository(Protocol):
    def get(self, cursor_id: str) -> SmartsheetStreamCursor | None: ...
    def save(self, cursor: SmartsheetStreamCursor) -> None: ...
