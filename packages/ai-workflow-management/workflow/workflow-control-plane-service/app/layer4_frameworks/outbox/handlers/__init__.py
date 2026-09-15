"""Outbox handlers — Phase 3 Sub 3.2.

One handler per ``OutboxRequested.kind``. Each module exposes
``async def perform(entry, ctx) -> dict`` and raises
``RetryableError`` / ``PermanentError`` from ``outbox_errors``.

Sub 3.2 ships the four handler modules with their default
implementations bound to the existing client interfaces. Subs 3.3 /
3.4 wire the producer side into the use cases that already make these
calls today — no behavioural change until then.
"""

from app.layer4_frameworks.outbox.handlers.file_delete_handler import (
    FileDeleteHandler,
)
from app.layer4_frameworks.outbox.handlers.file_upload_handler import (
    FileUploadHandler,
)
from app.layer4_frameworks.outbox.handlers.queue_enqueue_handler import (
    QueueEnqueueHandler,
)
from app.layer4_frameworks.outbox.handlers.stage_record_handler import (
    StageRecordHandler,
)

__all__ = [
    "FileDeleteHandler",
    "FileUploadHandler",
    "QueueEnqueueHandler",
    "StageRecordHandler",
]
