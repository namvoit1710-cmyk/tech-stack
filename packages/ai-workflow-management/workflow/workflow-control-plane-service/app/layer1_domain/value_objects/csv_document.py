"""``CsvDocument`` — a CSV artifact value object with byte-level edit operations.

Wraps the UTF-8 CSV ``content`` of a data artifact and exposes the row/column
mutations ``EditNodeDataUseCase`` performs per PATCH op. Each method parses,
transforms, and re-serialises — never mutating the wrapped bytes — and returns
the resulting CSV bytes (or ``(bytes, extra)`` for ops that also report
affected/inserted ids). No I/O, no state beyond the wrapped content, stdlib only.

Design invariants
=================
- **Immutable inputs.** Methods never mutate ``content``; they return new bytes.
- **Column-set stability.** Row ops (update/insert/delete) never change the
  header. Only column ops (``add_column`` / ``drop_column`` / ``rename_column``)
  mutate it.
- **Synthetic columns.** ``__row_id`` is always present on artifact CSVs
  (injected at creation via ``inject_row_ids``); ``__parent_row_id`` on
  nested-array CSVs — both preserved untouched. New ``__row_id`` values are
  integers (autoincrement per CSV — the file service requires integer ids).
- **UTF-8 text only.** Callers decode binary blobs elsewhere.

These are hot-path (per artifact per PATCH op): direct CSV manipulation, no pandas.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from typing import Any, ClassVar
from collections.abc import Iterable


ROW_ID_COLUMN = "__row_id"
PARENT_ROW_ID_COLUMN = "__parent_row_id"


@dataclass(frozen=True)
class CsvDocument:
    """A CSV artifact's content + its byte-level edit operations."""

    content: bytes = b""

    ROW_ID_COLUMN: ClassVar[str] = ROW_ID_COLUMN
    PARENT_ROW_ID_COLUMN: ClassVar[str] = PARENT_ROW_ID_COLUMN

    # ── parsing helpers (private) ─────────────────────────────────────

    def _read(self) -> tuple[list[str], list[dict[str, str]]]:
        """Return ``(header, rows_as_dicts)``. Empty content → empty header + rows."""
        if not self.content:
            return [], []
        text = self.content.decode("utf-8")
        if not text:
            return [], []
        reader = csv.DictReader(io.StringIO(text))
        header = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
        return header, rows

    @staticmethod
    def _serialise_cell(value: Any) -> Any:
        """Normalise a cell value for ``csv.DictWriter``: ``None`` → "", dict/list/
        tuple/set → JSON text (so containers round-trip), primitives pass through."""
        if value is None:
            return ""
        if isinstance(value, (dict, list, tuple, set)):
            if isinstance(value, (tuple, set)):
                value = list(value)
            return json.dumps(value, ensure_ascii=False, default=str)
        return value

    @classmethod
    def _write(cls, header: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
        """Serialise ``header`` + ``rows`` to CSV bytes (cells via ``_serialise_cell``)."""
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({col: cls._serialise_cell(row.get(col)) for col in header})
        return out.getvalue().encode("utf-8")

    @staticmethod
    def _max_int_row_id(rows: list[dict[str, Any]]) -> int:
        """Largest existing integer ``__row_id`` across ``rows`` (0 when none).

        New ids are ``max + 1`` (file service requires integer ids); non-integer /
        missing ids are ignored, so a legacy UUID-id CSV restarts cleanly at 1.
        """
        mx = 0
        for r in rows:
            raw = str(r.get(ROW_ID_COLUMN, "")).strip()
            if raw.lstrip("-").isdigit():
                v = int(raw)
                if v > mx:
                    mx = v
        return mx

    # ── public API ────────────────────────────────────────────────────

    @classmethod
    def from_rows(cls, header: list[str], rows: Iterable[dict[str, Any]]) -> bytes:
        """Serialise ``header`` + ``rows`` directly to CSV bytes (no existing
        content). For callers that build an artifact from a freshly-computed
        header + row set rather than editing existing content."""
        return cls._write(header, rows)

    def inject_row_ids(
        self, rows_needing_id: list[dict[str, Any]] | None = None,
    ) -> bytes:
        """Ensure every row has a ``__row_id``.

        If ``rows_needing_id`` is supplied, it is appended after the existing rows
        (each gets a fresh id unless it already has one). If None, rows in
        ``content`` without ``__row_id`` get one backfilled.
        """
        header, rows = self._read()
        if ROW_ID_COLUMN not in header:
            header = [ROW_ID_COLUMN] + header
        next_id = self._max_int_row_id(rows + list(rows_needing_id or [])) + 1
        for row in rows:
            if not row.get(ROW_ID_COLUMN):
                row[ROW_ID_COLUMN] = str(next_id)
                next_id += 1
        if rows_needing_id:
            for row in rows_needing_id:
                if not row.get(ROW_ID_COLUMN):
                    row[ROW_ID_COLUMN] = str(next_id)
                    next_id += 1
                rows.append(row)
        return self._write(header, rows)

    def list_row_ids(self) -> list[str]:
        """Extract ``__row_id`` values in file order."""
        _, rows = self._read()
        return [r.get(ROW_ID_COLUMN, "") for r in rows]

    def list_columns(self) -> list[str]:
        """Return the CSV header (column names) in file order; ``[]`` when empty."""
        header, _ = self._read()
        return header

    def update_cell_by_row_id(self, *, row_id: str, column: str, value: Any) -> bytes:
        """Set ``column = value`` on the row whose ``__row_id == row_id``.

        No matching row → unchanged (silent no-op for resilience during concurrent
        deletes). Raises ``ValueError`` if ``column`` is not in the header.
        """
        header, rows = self._read()
        if column not in header:
            raise ValueError(f"column {column!r} not in header {header!r}")
        for row in rows:
            if row.get(ROW_ID_COLUMN) == row_id:
                row[column] = value
                break
        return self._write(header, rows)

    def update_rows_by_ids(
        self, *, row_ids: list[str], assignments: dict[str, Any],
    ) -> tuple[bytes, int]:
        """Apply ``assignments`` to every row whose id is in ``row_ids``.

        Returns ``(new_bytes, rows_affected)``. Raises ``ValueError`` if any
        assignment key is not in the header.
        """
        header, rows = self._read()
        for col in assignments:
            if col not in header:
                raise ValueError(f"column {col!r} not in header {header!r}")
        id_set = set(row_ids)
        affected = 0
        for row in rows:
            if row.get(ROW_ID_COLUMN) in id_set:
                row.update(assignments)
                affected += 1
        return self._write(header, rows), affected

    def insert_rows(
        self, new_rows: list[dict[str, Any]], *, parent_row_id: str | None = None,
    ) -> tuple[bytes, list[str]]:
        """Append ``new_rows`` (each auto-assigned a fresh ``__row_id``).

        ``parent_row_id`` (nested artifact insert) sets ``__parent_row_id`` on each
        inserted row. Unknown columns are dropped. Returns ``(new_bytes, ids)``.
        """
        header, rows = self._read()
        if not header:
            keys: list[str] = []
            for row in new_rows:
                for k in row:
                    if k not in keys:
                        keys.append(k)
            header = [ROW_ID_COLUMN]
            if parent_row_id is not None:
                header.append(PARENT_ROW_ID_COLUMN)
            for k in keys:
                if k not in (ROW_ID_COLUMN, PARENT_ROW_ID_COLUMN):
                    header.append(k)
        assigned_ids: list[str] = []
        next_id = self._max_int_row_id(rows) + 1
        for raw in new_rows:
            row = {col: raw.get(col) for col in header if col in raw}
            row[ROW_ID_COLUMN] = str(next_id)
            next_id += 1
            if parent_row_id is not None:
                row[PARENT_ROW_ID_COLUMN] = parent_row_id
            assigned_ids.append(row[ROW_ID_COLUMN])
            rows.append(row)
        return self._write(header, rows), assigned_ids

    def upsert_scalar_row(self, data: dict[str, Any]) -> tuple[bytes, str]:
        """Upsert the single data row of an object (scalar) CSV, auto-expanding the
        schema. Existing header columns are preserved; keys not yet in the header
        are appended as new columns. Returns ``(new_bytes, row_id)``.
        """
        header, rows = self._read()
        if ROW_ID_COLUMN not in header:
            header = [ROW_ID_COLUMN] + header
        for key in data:
            if key not in header:
                header.append(key)
        if rows:
            row = dict(rows[0])
            row.update(data)
            row_id = str(row.get(ROW_ID_COLUMN) or "1")
            row[ROW_ID_COLUMN] = row_id
            out_rows = [row] + rows[1:]
        else:
            row_id = "1"
            row = {ROW_ID_COLUMN: row_id}
            row.update(data)
            out_rows = [row]
        return self._write(header, out_rows), row_id

    def delete_rows_by_ids(self, row_ids: list[str]) -> tuple[bytes, int]:
        """Remove rows whose id appears in ``row_ids``. Returns ``(new_bytes, removed)``."""
        header, rows = self._read()
        id_set = set(row_ids)
        filtered = [r for r in rows if r.get(ROW_ID_COLUMN) not in id_set]
        return self._write(header, filtered), len(rows) - len(filtered)

    def add_column(self, *, column: str, default_value: Any = None) -> bytes:
        """Append ``column`` after existing columns; backfill with ``default_value``.
        Raises ``ValueError`` if ``column`` already exists."""
        header, rows = self._read()
        if column in header:
            raise ValueError(f"column {column!r} already exists")
        header = list(header) + [column]
        for row in rows:
            row[column] = default_value
        return self._write(header, rows)

    def drop_column(self, column: str) -> bytes:
        """Remove ``column`` from the header and every row.
        Raises ``ValueError`` if ``column`` is not in the header."""
        header, rows = self._read()
        if column not in header:
            raise ValueError(f"column {column!r} not in header {header!r}")
        new_header = [c for c in header if c != column]
        for row in rows:
            row.pop(column, None)
        return self._write(new_header, rows)

    def rename_column(self, *, old_name: str, new_name: str) -> bytes:
        """Rename ``old_name`` to ``new_name`` (header + each row). Raises
        ``ValueError`` if ``old_name`` absent or ``new_name`` would collide."""
        header, rows = self._read()
        if old_name not in header:
            raise ValueError(f"column {old_name!r} not in header {header!r}")
        if new_name != old_name and new_name in header:
            raise ValueError(f"column {new_name!r} already exists — would collide")
        new_header = [new_name if c == old_name else c for c in header]
        for row in rows:
            if old_name in row:
                row[new_name] = row.pop(old_name)
        return self._write(new_header, rows)

    def merge_scalars(self, scalars: dict[str, Any]) -> bytes:
        """Update the single-row scalar CSV by setting each ``column=value``.
        Keys not in the header are added as new columns; existing are overwritten."""
        header, rows = self._read()
        if not rows:
            row: dict[str, Any] = {ROW_ID_COLUMN: "1"}
            row.update(scalars)
            header = [ROW_ID_COLUMN] + [c for c in scalars if c != ROW_ID_COLUMN]
            return self._write(header, [row])
        row = rows[0]
        header = list(header)
        for col, val in scalars.items():
            if col not in header:
                header.append(col)
            row[col] = val
        return self._write(header, [row] + rows[1:])
