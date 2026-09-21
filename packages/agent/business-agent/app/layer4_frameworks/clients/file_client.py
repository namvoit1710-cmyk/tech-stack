import httpx
from typing import Optional
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.file_client_interface import IFileClient
from app.layer1_domain.entities.file_client import FileData


class FileClient(IFileClient):
    def __init__(self, base_url: str, logger: ILogger) -> None:
        self._base_url = base_url.rstrip("/")
        self._logger = logger

    async def get_content_type(
        self,
        file_id: str,
    ) -> Optional[str]:
        if not self._base_url:
            self._logger.info("Base URL not configured")
            return None

        url = f"{self._base_url}/api/v1/files/{file_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url,
                    headers={"accept": "application/json"},
                )
                resp.raise_for_status()

            data = resp.json()
            current_version = data.get("current_version") or {}
            content_type = current_version.get("content_type")
            return content_type if isinstance(content_type, str) and content_type else None

        except Exception as exc:
            self._logger.info(f"Failed to get file details: {exc}")
            return None

    async def get_data_by_odata(
        self,
        file_id: str,
        top: int,
    ) -> Optional[FileData]:
        if not self._base_url:
            self._logger.info("Base URL not configured")
            return None

        url = f"{self._base_url}/api/v1/odata/{file_id}/data"

        params = {"$top": top}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    url,
                    headers={"accept": "application/json"},
                    params=params,
                )
                resp.raise_for_status()

                return FileData.from_dict(resp.json())

        except Exception as exc:
            self._logger.info(f"Failed to get odata file data: {exc}")
            return None

    async def download_file(
        self,
        file_id: str,
    ) -> Optional[bytes]:
        if not self._base_url:
            self._logger.info("Base URL not configured")
            return None

        url = f"{self._base_url}/api/v1/download/{file_id}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()

                return resp.content

        except Exception as exc:
            self._logger.info(f"Failed to download file: {exc}")
            return None
