from typing import Any, Optional, Protocol


class IFileServiceClient(Protocol):
    """Port for uploading/downloading files via the file service."""

    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "text/csv",
        *,
        idempotency_key: Optional[str] = None,
    ) -> dict[str, Any]:
        """Upload file bytes. Returns {file_id, filename, content_type, size_bytes, status}.

        Phase 3 Sub 3.3 / 3.6: when ``idempotency_key`` is provided the
        file service treats two uploads with the same key as the same
        upload — the second call returns the existing ``file_id`` rather
        than allocating a new one. Required for the outbox dispatcher's
        replay-safety guarantee (Sub 3.6 chaos test).
        """
        ...

    async def download_file(self, file_id: str) -> Optional[bytes]:
        """Download file content by ID. Returns None if not found."""
        ...

    async def delete_file(self, file_id: str) -> dict[str, Any]:
        """Delete a file by ID. Returns {file_id, status}."""
        ...

    async def get_presigned_url(self, file_id: str, operation: str = "download", expires_in: int = 3600) -> dict[str, Any]:
        """Get a presigned URL for direct download/upload. Returns {file_id, url, expires_in, operation}."""
        ...

    async def query_file_data(self, file_id: str, query_params: dict[str, str]) -> dict[str, Any]:
        """OData query over CSV data. Returns {file_id, total_matches, data: [...]}."""
        ...

    async def head_file(self, file_id: str) -> Optional[dict[str, Any]]:
        """Return file metadata without content.

        Returns ``{file_id, size_bytes, content_type, columns, row_count}`` when the
        file exists; ``None`` if not found. Used by ``replace_artifact`` validation
        (Unit D) to verify an externally-uploaded file_id and inspect its columns
        before swapping it into a node's ``__file_ref`` tree.
        """
        ...

    async def get_columns_via_odata(self, file_id: str) -> list[str]:
        """Return a CSV file's column list using a cheap OData ``$top=1`` probe.

        Avoids downloading the full file body — only the first row is read,
        and its keys give the column names. ``_row_index`` and other
        OData fixture keys are filtered out. Returns ``[]`` on empty files,
        missing file_id, or any error, so callers can fall back to their
        own schema source.
        """
        ...

    async def patch_file_data(self, file_id: str, patch_payload: dict[str, Any]) -> dict[str, Any]:
        """Apply cell edits (mock/local only). Returns {new_file_id, rows_modified, ...}."""
        ...

    async def aclose(self) -> None:
        """Close the underlying HTTP client. No-op for in-memory mocks."""
        ...
