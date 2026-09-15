"""``file_delete`` outbox handler — Phase 3 Sub 3.2.

Triggers ``IFileServiceClient.delete_file`` for the orphan-cleanup
branch of the DATA_EDIT rerun path. Naturally idempotent — repeated
deletes return the same outcome.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.layer4_frameworks.outbox.outbox_errors import (
    PermanentError,
    RetryableError,
)


class IFileServiceLike(Protocol):
    async def delete_file(self, file_id: str) -> dict[str, Any]: ...


class FileDeleteHandler:
    def __init__(self, file_service: IFileServiceLike) -> None:
        self._file_service = file_service

    async def perform(self, entry: Any) -> dict[str, Any]:
        file_id = str(entry.payload.get("file_id") or "")
        if not file_id:
            raise PermanentError("file_delete payload missing file_id")
        try:
            await self._file_service.delete_file(file_id)
        except Exception as exc:
            raise RetryableError(f"file_service.delete_file failed: {exc}") from exc
        return {}
