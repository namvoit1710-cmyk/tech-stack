"""In-memory Smartsheet stream-cursor repository (smartsheet-event-trigger S1)."""

from __future__ import annotations

from app.layer1_domain.entities.smartsheet_stream_cursor import SmartsheetStreamCursor
from app.layer2_application.repositories.smartsheet_cursor_repository_interface import (
    ISmartsheetCursorRepository,
)


class MemorySmartsheetCursorRepository(ISmartsheetCursorRepository):
    def __init__(self) -> None:
        self._store: dict[str, SmartsheetStreamCursor] = {}

    def get(self, cursor_id: str) -> SmartsheetStreamCursor | None:
        return self._store.get(cursor_id)

    def save(self, cursor: SmartsheetStreamCursor) -> None:
        self._store[cursor.id] = cursor
