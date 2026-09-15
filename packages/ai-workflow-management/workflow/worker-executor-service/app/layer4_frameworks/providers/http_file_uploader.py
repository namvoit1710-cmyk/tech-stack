"""POST bytes to the file service; return the stored ``file_id``.

The executor's only outbound dependencies were the control plane and the
workers. This adds a third, for exactly one job: when a worker result is too
large to publish, upload it and relay a pointer instead of throwing the work
away (see ``HandleWorkerCallbackUseCase._offload_result``).

Deliberately SYNCHRONOUS. ``HandleWorkerCallbackUseCase.execute`` is a sync use
case, and this is called on a path that has already failed once — an event-loop
hop would buy nothing and complicate the failure ladder. ``httpx`` is already a
dependency of this service (``requirements.txt``), so nothing new is pulled in.

Wire contract mirrors ``worker_sdk``'s uploader so the file service sees one
shape from both sides: ``POST {base}/api/v1/upload?filename=<name>`` with a
multipart ``file`` part.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

_API_PREFIX = "/api/v1"


class HttpFileUploader:
    """Upload bytes, return a ``file_id`` — or ``""`` when none came back."""

    def __init__(
        self,
        file_service_url: str,
        timeout: float = 60.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self._base_url = file_service_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def upload(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/json",
        idempotency_key: Optional[str] = None,
    ) -> str:
        """Return the stored ``file_id``.

        Raises on transport/HTTP failure; the caller treats any exception as
        "offload not possible" and degrades to the pre-existing
        ``task.failed``. An empty ``file_id`` is returned rather than invented,
        so a file service that answers 200 with nothing still degrades instead
        of publishing a pointer to nowhere.
        """
        url = f"{self._base_url}{_API_PREFIX}/upload"
        files = {"file": (filename, file_bytes, content_type)}
        headers: dict[str, str] = {}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            resp = client.post(
                url, files=files, params={"filename": filename},
                headers=headers or None,
            )
            resp.raise_for_status()
            body: dict[str, Any] = resp.json() or {}
            return body.get("file_id", "")
