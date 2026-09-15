"""HTTP adapter for the external data-factory service.

Endpoints:
  POST /api/v1/transformation/rows   — row-level insert/delete
  POST /api/v1/transformation         — column-level transforms

Version model (data-factory v2): a row transform mutates the source file
*in place*, creating a new file version. The response no longer carries a
``file_id`` — the new revision is addressed by ``version_id``, encoded in
``download_url`` as ``.../api/v1/files/versions/{version_id}/download``. The
caller's ``file_id`` stays stable as the artifact identity; ``version_id``
is the new revision pointer.
"""
import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.layer2_application.interfaces.data_factory_client_interface import IDataFactoryClient

logger = logging.getLogger(__name__)

_API_PREFIX = "/api/v1/transformation"


def _extract_version_id(download_url: str) -> str:
    """Extract the file version id from a v2 download URL.

    The row-transform response addresses the mutated artifact by version
    under ``.../api/v1/files/versions/{version_id}/download``. Returns the
    segment immediately after ``versions``; ``""`` when the URL carries no
    such segment. Uses the URL *path* only, so the scheme/host can never be
    mistaken for an id.
    """
    try:
        raw_path = urlparse(download_url).path if "://" in download_url else download_url
        segments = [s for s in raw_path.strip("/").split("/") if s]
        if "versions" in segments:
            idx = segments.index("versions")
            if idx + 1 < len(segments):
                return segments[idx + 1]
        return ""
    except Exception:
        return ""


class HttpDataFactoryClient(IDataFactoryClient):
    def __init__(self, data_factory_url: str) -> None:
        self._base_url = data_factory_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

    async def transform_rows(
        self,
        file_id: str,
        operations: list[dict[str, Any]],
        file_format: str = "csv",
        version_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{_API_PREFIX}/rows"
        payload: dict[str, Any] = {
            "file_id": file_id,
            "file_format": file_format,
            "operations": operations,
        }
        # Optional in v2: omitting version_id makes the factory read the
        # latest file version; sending it pins the read/write to that version.
        if version_id:
            payload["version_id"] = version_id
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            # v2 encodes the new revision in download_url
            # (.../files/versions/{version_id}/download); surface it.
            if not data.get("version_id") and data.get("download_url"):
                data["version_id"] = _extract_version_id(data["download_url"])
            # v2 omits file_id (the factory mutates in place). Echo the input
            # id so the caller keeps a stable artifact identity; version_id
            # carries the new revision.
            if data.get("success") and not data.get("file_id"):
                data["file_id"] = file_id
            return data
        except Exception as exc:
            logger.error("POST %s/rows failed: %s", _API_PREFIX, exc)
            return {"success": False, "message": str(exc)}

    async def transform_data(
        self,
        file_id: str,
        rules: list[dict[str, Any]],
        file_format: str = "csv",
        output_format: str = "csv",
    ) -> dict[str, Any]:
        url = f"{self._base_url}{_API_PREFIX}"
        payload = {
            "file_path": file_id,
            "file_format": file_format,
            "output_format": output_format,
            "rules": rules,
        }
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.error("POST %s failed: %s", _API_PREFIX, exc)
            return {"success": False, "message": str(exc)}
