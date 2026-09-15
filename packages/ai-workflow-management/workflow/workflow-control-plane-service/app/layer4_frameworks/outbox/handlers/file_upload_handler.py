"""``file_upload`` outbox handler — Phase 3 Sub 3.2.

Performs the real ``IFileServiceClient.upload_file`` call for a
DATA_EDIT mutation. Bytes reach this handler through a per-pod
``OutboxBytesChannel`` populated by the producing handler — the
event payload only carries metadata (filename, content_hash,
content_type) so the event log stays small.

Replay safety: when the channel is empty (cold pod, replay across
restart), the producing handler re-runs and re-populates the
channel; the file-service's ``content_hash`` idempotency dedupes
the upload to the existing ``file_id``.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.layer4_frameworks.outbox.outbox_errors import (
    PermanentError,
    RetryableError,
)


class IBytesChannel(Protocol):
    def pop(self, outbox_id: str) -> bytes | None: ...


class IFileServiceLike(Protocol):
    async def upload_file(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "text/csv",
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]: ...


class FileUploadHandler:
    """Handler for ``OutboxRequested.kind == "file_upload"``."""

    def __init__(
        self,
        file_service: IFileServiceLike,
        bytes_channel: IBytesChannel,
    ) -> None:
        self._file_service = file_service
        self._bytes_channel = bytes_channel

    async def perform(self, entry: Any) -> dict[str, Any]:
        """Upload the bytes registered for ``entry.outbox_id``.

        ``entry`` is an ``OutboxEntry``. Returns the kind-specific
        result ``{"file_id": <new id>}`` for ``OutboxResolved.result``.
        Phase 3 Sub 3.6: forwards ``entry.idempotency_key`` (the
        producer-computed ``content_hash``) to the file service so
        a redelivered upload after Executor crash + restart returns
        the original ``file_id`` rather than allocating an orphan.
        """
        bytes_payload = self._bytes_channel.pop(entry.outbox_id)
        if bytes_payload is None:
            # Replay path: channel empty because the producing handler
            # has not re-populated it yet on this pod. Retry — the next
            # poll picks it up after the producer re-runs.
            raise RetryableError(
                f"bytes channel empty for outbox_id={entry.outbox_id}",
            )
        filename = str(entry.payload.get("filename") or "")
        content_type = str(entry.payload.get("content_type") or "text/csv")
        if not filename:
            raise PermanentError("file_upload payload missing filename")
        idem_key = entry.idempotency_key or None
        try:
            response = await self._file_service.upload_file(
                bytes_payload, filename, content_type,
                idempotency_key=idem_key,
            )
        except Exception as exc:
            raise RetryableError(f"file_service.upload_file failed: {exc}") from exc
        file_id = str(response.get("file_id") or response.get("id") or "")
        if not file_id:
            raise PermanentError(
                f"file_service.upload_file returned no file_id: {response!r}",
            )
        return {"file_id": file_id}
