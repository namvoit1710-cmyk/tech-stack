"""In-memory data-factory client for testing and mock mode."""
import csv
import io
from typing import Any

from app.layer2_application.interfaces.data_factory_client_interface import IDataFactoryClient


class MockDataFactoryClient(IDataFactoryClient):
    """Mock that operates on an injected file_service_client's in-memory store."""

    def __init__(self, file_service_client: Any=None) -> None:
        self._file_client = file_service_client

    async def transform_rows(
        self,
        file_id: str,
        operations: list[dict[str, Any]],
        file_format: str = "csv",
        version_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._file_client:
            return {"success": False, "message": "File service not configured"}

        raw = await self._file_client.download_file(file_id)
        if raw is None:
            return {"success": False, "message": f"File {file_id} not found"}

        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        columns = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
        original_count = len(rows)
        affected = 0

        for op in operations:
            op_type = op.get("operation")
            identifier = op.get("row_identifier", {})

            if op_type == "delete":
                if identifier.get("type") == "index":
                    idx = identifier.get("index")
                    if idx is not None and 0 <= idx < len(rows):
                        rows.pop(idx)
                        affected += 1
                elif identifier.get("type") == "values":
                    match_vals = identifier.get("match_values", {})
                    before = len(rows)
                    rows = [
                        r for r in rows
                        if not all(r.get(k) == str(v) for k, v in match_vals.items())
                    ]
                    affected += before - len(rows)

            elif op_type == "insert":
                data = op.get("data", {})
                position = op.get("position", "after")
                new_row = {c: str(data.get(c, "")) for c in columns}

                if identifier.get("type") == "index":
                    idx = identifier.get("index", len(rows))
                    if position == "before":
                        rows.insert(idx, new_row)
                    elif position == "after":
                        rows.insert(idx + 1, new_row)
                    elif position == "at_index":
                        rows.insert(idx, new_row)
                    else:
                        rows.append(new_row)
                else:
                    rows.append(new_row)
                affected += 1

        # Write result CSV and upload
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")

        upload_result = await self._file_client.upload_file(csv_bytes, f"transformed-{file_id}.csv", "text/csv")
        new_file_id = upload_result.get("file_id", "")

        return {
            "success": True,
            "file_id": new_file_id,
            # In-memory mock has no version chain; the new file_id is the
            # artifact. version_id stays empty so callers fall back to
            # file_id→latest reads.
            "version_id": "",
            "affected_rows": affected,
            "total_rows": len(rows),
            "message": f"Row transformations completed. Original: {original_count}, Final: {len(rows)}",
            "download_url": "",
        }

    async def transform_data(
        self,
        file_id: str,
        rules: list[dict[str, Any]],
        file_format: str = "csv",
        output_format: str = "csv",
    ) -> dict[str, Any]:
        # Mock supports the ``mapping`` rule with ``new_col`` (the subset
        # PATCH /data uses to auto-add columns). Other rule types are no-ops.
        # The new file_id is the artifact; version_id stays empty (no version
        # chain in the in-memory mock) and identity is echoed under both the
        # legacy ``file_id`` and the v2 ``result.output_file_id``.
        if not self._file_client:
            return {"success": False, "message": "File service not configured"}

        raw = await self._file_client.download_file(file_id)
        if raw is None:
            return {"success": False, "message": f"File {file_id} not found"}

        reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
        columns = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]

        for rule in rules or []:
            if rule.get("type") != "mapping":
                continue
            new_col = (rule.get("params") or {}).get("new_col")
            if new_col and new_col not in columns:
                columns.append(new_col)
                for r in rows:
                    r[new_col] = ""

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        csv_bytes = buf.getvalue().encode("utf-8")

        upload_result = await self._file_client.upload_file(
            csv_bytes, f"transformed-{file_id}.csv", "text/csv"
        )
        new_file_id = upload_result.get("file_id", "")

        return {
            "success": True,
            "file_id": new_file_id,
            "result": {
                "success": True,
                "output_file_id": new_file_id,
                "output_version_id": "",
                "total_columns": len(columns),
                "total_rows": len(rows),
            },
            "message": "Mock transform_data — applied mapping rules",
        }
