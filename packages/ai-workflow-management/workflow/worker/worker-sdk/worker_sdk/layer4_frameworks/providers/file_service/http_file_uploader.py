"""Worker-side file-service upload client (SA-1905 C2).

Uploads CSV bytes to the file service and returns the ``file_id`` — matching the
control-plane ``HttpFileServiceClient.upload_file`` contract so a file_ref the
worker produces is downloadable/resolvable by the CP:

    POST {base}/api/v1/upload?filename=<name>   multipart/form-data ("file" part)
    X-Idempotency-Key: <content-hash>           (dedupe replayed uploads)
    -> {"file_id": "...", "filename": ..., "size_bytes": ..., "status": ...}

Async (httpx.AsyncClient) so it doesn't block the worker event loop during the
upload. A ``transport`` may be injected for unit tests (httpx.MockTransport).
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

import httpx

_API_PREFIX = "/api/v1"


def content_idempotency_key(data: bytes) -> str:
    """Content-addressable dedupe key (mirrors the CP's ``_content_key``)."""
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class HttpFileUploader:
    """POST bytes to the file service; return the stored ``file_id``."""

    def __init__(
        self,
        file_service_url: str,
        timeout: float = 60.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = file_service_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    async def upload(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "text/csv",
        idempotency_key: Optional[str] = None,
    ) -> str:
        """Upload *file_bytes* and return the file_id (empty string on failure
        to produce a file_id, so the caller can fall back to inline output)."""
        url = f"{self._base_url}{_API_PREFIX}/upload"
        files = {"file": (filename, file_bytes, content_type)}
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            resp = await client.post(
                url, files=files, params={"filename": filename},
                headers=headers or None,
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json() or {}
            return body.get("file_id", "")
