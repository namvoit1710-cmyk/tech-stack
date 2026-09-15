"""SAP HANA implementation of ``ISmartsheetCursorRepository`` (smartsheet-event-trigger S1).

Durable stream cursor for the Smartsheet Event Reporting poller. Mirrors the HANA
repo template (``HanaWebhookRegistrationRepository`` / ``HanaTimerRepository``):

- Constructor takes an ``IDatabaseConnection`` exposing ``execute_query(sql, params)``
  / ``execute_write(sql, params)`` with ``:p0`` named placeholders.
- ``save`` is an upsert via delete-then-insert on ``ID`` (HANA has no portable
  ``ON CONFLICT``).
- Rows are read with both lower-case (``HanaConnectionManager``) and upper-case
  (stub) column names.

Persists to ``AIW_SMARTSHEET_STREAM_CURSOR`` (migration
``053_create_aiw_smartsheet_stream_cursor.sql``).
"""

from __future__ import annotations

from typing import Any

from app.layer1_domain.entities.smartsheet_stream_cursor import SmartsheetStreamCursor
from app.layer2_application.repositories.smartsheet_cursor_repository_interface import (
    ISmartsheetCursorRepository,
)
from app.layer4_frameworks.persistence.hana.async_repo_mixin import AsyncRepoMixin

_TABLE = "AIW_SMARTSHEET_STREAM_CURSOR"
_COLS = "ID, STREAM_POSITION, UPDATED_AT"
_DELETE_SQL = f"DELETE FROM {_TABLE} WHERE ID = :p0"
_INSERT_SQL = f"INSERT INTO {_TABLE} ({_COLS}) VALUES (:p0, :p1, :p2)"
_SELECT_SQL = f"SELECT {_COLS} FROM {_TABLE} WHERE ID = :p0"


class HanaSmartsheetCursorRepository(AsyncRepoMixin, ISmartsheetCursorRepository):
    """SAP HANA-backed ``ISmartsheetCursorRepository``."""

    def __init__(self, db: Any) -> None:
        self._db = db

    def get(self, cursor_id: str) -> SmartsheetStreamCursor | None:
        rows = self._db.execute_query(_SELECT_SQL, (cursor_id,))
        return self._row_to_cursor(rows[0]) if rows else None

    def save(self, cursor: SmartsheetStreamCursor) -> None:
        """Upsert ``id`` — delete any prior row then insert."""
        self._db.execute_write(_DELETE_SQL, (cursor.id,))
        self._db.execute_write(
            _INSERT_SQL, (cursor.id, cursor.stream_position, cursor.updated_at)
        )

    @staticmethod
    def _get(row: dict, *keys: str) -> Any:
        for k in keys:
            if k in row and row[k] is not None:
                return row[k]
        return None

    @classmethod
    def _row_to_cursor(cls, row: dict) -> SmartsheetStreamCursor:
        return SmartsheetStreamCursor(
            id=str(cls._get(row, "id", "ID") or ""),
            stream_position=str(cls._get(row, "stream_position", "STREAM_POSITION") or ""),
            updated_at=str(cls._get(row, "updated_at", "UPDATED_AT") or ""),
        )


if __name__ == "__main__":
    # ponytail: in-memory fake IDatabaseConnection self-check — exercises the
    # upsert (one row, position advances) and row mapping without a live HANA.
    # Runs with `python hana_smartsheet_cursor_repo.py`.
    class _FakeDB:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        def execute_write(self, sql, params=()):
            if sql.strip().startswith("DELETE"):
                self.rows = [r for r in self.rows if r["ID"] != params[0]]
            else:
                self.rows.append(
                    {"ID": params[0], "STREAM_POSITION": params[1], "UPDATED_AT": params[2]}
                )

        def execute_query(self, sql, params=()):
            return [r for r in self.rows if r["ID"] == params[0]]

    repo = HanaSmartsheetCursorRepository(_FakeDB())
    assert repo.get("smartsheet") is None
    repo.save(SmartsheetStreamCursor(id="smartsheet", stream_position="pos-1", updated_at="t0"))
    got = repo.get("smartsheet")
    assert got is not None and got.stream_position == "pos-1", got
    # upsert: one row, position advances
    repo.save(SmartsheetStreamCursor(id="smartsheet", stream_position="pos-2", updated_at="t1"))
    assert repo.get("smartsheet").stream_position == "pos-2"
    assert len(repo._db.rows) == 1, repo._db.rows
    print("hana_smartsheet_cursor_repo self-check OK")
