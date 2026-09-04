from __future__ import annotations

import asyncio
from pathlib import Path
from shutil import copyfileobj
from tempfile import NamedTemporaryFile

from smart_service_sdk.layer4_frameworks.files.file_service_client import (
    content_hash,
    content_type_for_file_name,
    parse_csv_rows,
)


class LocalFileServiceClient:
    def __init__(self, root_directory: Path | str):
        self._root_directory = Path(root_directory)

    async def download_file(
        self,
        file_id: str,
        tenant_id: str | None = None,
        *,
        fallback_file_name: str | None = None,
        fallback_content_type: str | None = None,
    ) -> tuple[str, str, str]:
        return await asyncio.to_thread(
            self._download_file_sync,
            file_id,
            fallback_file_name,
            fallback_content_type,
        )

    def _download_file_sync(
        self,
        file_id: str,
        fallback_file_name: str | None = None,
        fallback_content_type: str | None = None,
    ) -> tuple[str, str, str]:
        root = self._root_directory.resolve()
        source_path = (root / file_id).resolve()

        try:
            source_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                "file_id resolves outside LOCAL_FILE_STORAGE_PATH"
            ) from exc

        if not source_path.is_file():
            raise FileNotFoundError(file_id)

        effective_file_name = fallback_file_name or source_path.name
        suffix = Path(effective_file_name).suffix or source_path.suffix or ".bin"
        temp_path = None
        try:
            with source_path.open("rb") as source_file, NamedTemporaryFile(
                mode="wb",
                suffix=suffix,
                delete=False,
            ) as temp_file:
                copyfileobj(source_file, temp_file)
                temp_path = temp_file.name
        except Exception:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)
            raise

        if temp_path is None:
            raise RuntimeError("Temporary download path was not created")

        return (
            temp_path,
            effective_file_name,
            fallback_content_type or content_type_for_file_name(effective_file_name),
        )

    def parse_csv_rows(self, file_path: str) -> list[dict[str, str]]:
        return parse_csv_rows(file_path)

    def content_hash(self, file_path: str) -> str:
        return content_hash(file_path)
