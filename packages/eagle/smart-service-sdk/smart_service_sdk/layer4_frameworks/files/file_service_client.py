from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx


def content_type_for_file_name(file_name: str) -> str:
    extension = Path(file_name).suffix.lower()
    if extension == ".csv":
        return "text/csv"
    if extension in {".md", ".markdown"}:
        return "text/markdown"
    if extension == ".json":
        return "application/json"
    if extension == ".txt":
        return "text/plain"
    if extension == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if extension == ".xls":
        return "application/vnd.ms-excel"
    if extension == ".pdf":
        return "application/pdf"
    return "application/octet-stream"


class FileServiceClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._download_path_template = "/api/v1/download/{file_id}"

    async def download_file(
        self,
        file_id: str,
        tenant_id: str | None = None,
        *,
        fallback_file_name: str | None = None,
        fallback_content_type: str | None = None,
    ) -> tuple[str, str, str]:
        url = self._base_url + self._download_path_template.format(file_id=file_id)
        params = {"tenant_id": tenant_id} if tenant_id else None
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
        file_name = (
            fallback_file_name
            or _file_name_from_headers(response)
            or file_id
        )
        suffix = Path(file_name).suffix or ".bin"
        with NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as temp_file:
            temp_file.write(response.content)
            temp_path = temp_file.name
        return (
            temp_path,
            file_name,
            fallback_content_type
            or response.headers.get("content-type")
            or content_type_for_file_name(file_name),
        )

    def parse_csv_rows(self, file_path: str) -> list[dict[str, str]]:
        return parse_csv_rows(file_path)

    def content_hash(self, file_path: str) -> str:
        return content_hash(file_path)


def parse_csv_rows(file_path: str) -> list[dict[str, str]]:
    with Path(file_path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return [
            {str(key): str(value or "") for key, value in row.items() if key}
            for row in reader
        ]


def content_hash(file_path: str) -> str:
    return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()


def _file_name_from_headers(response: httpx.Response) -> str | None:
    disposition = response.headers.get("content-disposition", "")
    if "filename=" not in disposition:
        return None
    return disposition.split("filename=", 1)[1].strip().strip('"')
