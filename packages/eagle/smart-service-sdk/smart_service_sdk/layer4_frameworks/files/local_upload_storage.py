from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class LocalUploadStorage:
    def __init__(self, root_directory: Path | str, max_upload_bytes: int | None = None):
        self._root_directory = Path(root_directory)
        self._max_upload_bytes = (
            max_upload_bytes if max_upload_bytes and max_upload_bytes > 0 else None
        )

    async def store(
        self,
        file_name: str,
        content: BinaryIO,
        *,
        tenant_id: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> tuple[str, str]:
        return await asyncio.to_thread(self._store_sync, file_name, content)

    async def delete(self, file_id: str, *, tenant_id: str | None = None) -> None:
        await asyncio.to_thread(self._delete_sync, file_id)

    def _store_sync(self, file_name: str, content: BinaryIO) -> tuple[str, str]:
        suffix = Path(file_name).suffix.lower()
        relative_path = Path("uploads") / f"{uuid4().hex}{suffix}"
        target_path = (self._root_directory / relative_path).resolve()
        root_path = self._root_directory.resolve()
        try:
            target_path.relative_to(root_path)
        except ValueError as exc:
            raise ValueError(
                "upload path resolves outside LOCAL_FILE_STORAGE_PATH"
            ) from exc

        target_path.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        bytes_written = 0
        try:
            content.seek(0)
        except Exception as exc:
            raise ValueError("Upload content stream must be seekable") from exc
        try:
            with target_path.open("wb") as target_file:
                while True:
                    chunk = content.read(1024 * 1024)
                    if not chunk:
                        break
                    if isinstance(chunk, str):
                        chunk = chunk.encode()
                    bytes_written += len(chunk)
                    if (
                        self._max_upload_bytes is not None
                        and bytes_written > self._max_upload_bytes
                    ):
                        raise ValueError(
                            f"Upload exceeds LOCAL_UPLOAD_MAX_BYTES={self._max_upload_bytes}"
                        )
                    digest.update(chunk)
                    target_file.write(chunk)
        except Exception:
            target_path.unlink(missing_ok=True)
            raise
        return relative_path.as_posix(), digest.hexdigest()

    def _delete_sync(self, file_id: str) -> None:
        target_path = (self._root_directory / Path(file_id)).resolve()
        root_path = self._root_directory.resolve()
        try:
            target_path.relative_to(root_path)
        except ValueError as exc:
            raise ValueError(
                "upload path resolves outside LOCAL_FILE_STORAGE_PATH"
            ) from exc

        target_path.unlink(missing_ok=True)
