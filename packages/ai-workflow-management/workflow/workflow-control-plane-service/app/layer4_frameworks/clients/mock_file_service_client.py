"""In-memory file service client for testing and mock mode."""
import csv
import io
import re
import uuid
from typing import Any, Optional

from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient


class MockFileServiceClient(IFileServiceClient):
    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        # Phase 3 Sub 3.6 — idempotency key registry. Maps a caller-
        # supplied ``idempotency_key`` to the ``file_id`` that the first
        # successful upload allocated. Subsequent uploads with the same
        # key return the same id without re-storing bytes — this is the
        # invariant the chaos test exercises.
        self._idem: dict[str, str] = {}

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "text/csv",
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        if idempotency_key:
            existing = self._idem.get(idempotency_key)
            if existing is not None:
                return {
                    "file_id": existing,
                    "filename": filename,
                    "content_type": content_type,
                    "deduped": True,
                }
        file_id = str(uuid.uuid4())
        self._store[file_id] = file_bytes
        if idempotency_key:
            self._idem[idempotency_key] = file_id
        return {"file_id": file_id, "filename": filename, "content_type": content_type}

    def list_files_by_idempotency_key(self, idempotency_key: str) -> list[str]:
        """Return the ``file_id``s allocated for ``idempotency_key``.

        Used by the Sub 3.6 chaos test to assert no orphan uploads
        landed when an Executor was killed mid-handler. With a working
        idempotency layer the list contains exactly one id even after
        a redelivered upload attempt.
        """
        fid = self._idem.get(idempotency_key)
        return [fid] if fid else []

    async def download_file(self, file_id: str) -> Optional[bytes]:
        return self._store.get(file_id)

    async def delete_file(self, file_id: str) -> dict[str, Any]:
        if file_id in self._store:
            del self._store[file_id]
            return {"file_id": file_id, "status": "deleted"}
        return {"file_id": file_id, "status": "not_found"}

    async def get_presigned_url(self, file_id: str, operation: str = "download", expires_in: int = 3600) -> dict[str, Any]:
        # Mock: return a fake local URL
        return {
            "file_id": file_id,
            "url": f"http://localhost:8001/files/{file_id}",
            "expires_in": expires_in,
            "operation": operation,
        }

    async def aclose(self) -> None:
        """No-op for the in-memory mock; method exists to match the protocol."""
        return

    async def head_file(self, file_id: str) -> Optional[dict[str, Any]]:
        raw = self._store.get(file_id)
        if raw is None:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Non-text / binary content — report size only.
            return {
                "file_id": file_id,
                "size_bytes": len(raw),
                "content_type": "application/octet-stream",
                "columns": [],
                "row_count": 0,
            }
        reader = csv.DictReader(io.StringIO(text))
        columns = list(reader.fieldnames or [])
        row_count = sum(1 for _ in reader)
        return {
            "file_id": file_id,
            "size_bytes": len(raw),
            "content_type": "text/csv",
            "columns": columns,
            "row_count": row_count,
        }

    # ----- OData query (mock) -----

    async def query_file_data(self, file_id: str, query_params: dict[str, str]) -> dict[str, Any]:
        """In-memory OData-lite query over stored CSV bytes."""
        raw = self._store.get(file_id)
        if raw is None:
            return {"file_id": file_id, "total_matches": 0, "data": []}

        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        rows = [dict(r) for r in reader]

        # Attach _row_index (original position)
        for idx, row in enumerate(rows):
            row["_row_index"] = str(idx)

        # Apply simple $filter (supports eq and contains)
        filter_str = query_params.get("$filter", "")
        if filter_str:
            rows = self._apply_filter(rows, filter_str)

        total_matches = len(rows)

        # $orderby
        orderby = query_params.get("$orderby", "")
        if orderby:
            parts = orderby.strip().split()
            col = parts[0]
            desc = len(parts) > 1 and parts[1].lower() == "desc"
            rows.sort(key=lambda r: r.get(col, ""), reverse=desc)

        # $skip / $top
        skip = int(query_params.get("$skip", "0"))
        top = int(query_params.get("$top", "100"))
        rows = rows[skip: skip + top]

        return {"file_id": file_id, "total_matches": total_matches, "data": rows}

    async def get_columns_via_odata(self, file_id: str) -> list[str]:
        if not file_id:
            return []
        resp = await self.query_file_data(file_id, {"$top": "1"})
        rows = resp.get("data", [])
        if not rows:
            return []
        return [k for k in rows[0].keys() if k and not k.startswith("_")]

    @staticmethod
    def _apply_filter(rows: list[dict], filter_str: str) -> list[dict]:
        """Parse a single OData $filter clause (eq / contains)."""
        # contains(field,'value')
        m = re.match(r"contains\((\w+),\s*'([^']*)'\)", filter_str)
        if m:
            field, val = m.group(1), m.group(2)
            return [r for r in rows if val.lower() in r.get(field, "").lower()]

        # field eq 'value'
        m = re.match(r"(\w+)\s+eq\s+'([^']*)'", filter_str)
        if m:
            field, val = m.group(1), m.group(2)
            return [r for r in rows if r.get(field, "") == val]

        # field gt 'value'
        m = re.match(r"(\w+)\s+gt\s+'([^']*)'", filter_str)
        if m:
            field, val = m.group(1), m.group(2)
            return [r for r in rows if r.get(field, "") > val]

        # field lt 'value'
        m = re.match(r"(\w+)\s+lt\s+'([^']*)'", filter_str)
        if m:
            field, val = m.group(1), m.group(2)
            return [r for r in rows if r.get(field, "") < val]

        return rows

    # ----- Patch (mock) -----

    async def patch_file_data(self, file_id: str, patch_payload: dict[str, Any]) -> dict[str, Any]:
        """Apply edits/additions/deletions in-memory, store new file bytes."""
        raw = self._store.get(file_id)
        if raw is None:
            return {}

        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        columns = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

        edits = patch_payload.get("edits", [])
        new_rows = patch_payload.get("new_rows", [])
        delete_indices = set(patch_payload.get("delete_row_indices", []))

        # Delete rows
        if delete_indices:
            rows = [r for i, r in enumerate(rows) if i not in delete_indices]

        # Apply cell edits (by original row_index — build index map first)
        # Since deletions change positions, we need to work on original indices.
        # Re-read from scratch when there are both deletes and edits.
        if edits and delete_indices:
            # Re-derive: reload, apply edits on original, then delete
            reader2 = csv.DictReader(io.StringIO(raw.decode("utf-8")))
            rows_orig = [dict(r) for r in reader2]
            for edit in edits:
                idx = edit.get("row_index", -1)
                col = edit.get("column", "")
                val = edit.get("value", "")
                if 0 <= idx < len(rows_orig) and col in rows_orig[idx]:
                    rows_orig[idx][col] = val
            rows = [r for i, r in enumerate(rows_orig) if i not in delete_indices]
        elif edits:
            for edit in edits:
                idx = edit.get("row_index", -1)
                col = edit.get("column", "")
                val = edit.get("value", "")
                if 0 <= idx < len(rows) and col in rows[idx]:
                    rows[idx][col] = val

        # Append new rows
        for nr in new_rows:
            row = {c: nr.get(c, "") for c in columns}
            rows.append(row)

        # Write back to CSV bytes
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")

        new_file_id = str(uuid.uuid4())
        self._store[new_file_id] = csv_bytes

        return {
            "new_file_id": new_file_id,
            "rows_modified": len(edits),
            "rows_added": len(new_rows),
            "rows_deleted": len(delete_indices),
        }
