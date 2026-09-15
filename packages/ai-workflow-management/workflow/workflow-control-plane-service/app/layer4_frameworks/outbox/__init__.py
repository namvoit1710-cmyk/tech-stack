"""Outbox dispatcher infrastructure — Phase 3 Sub 3.2.

The producer side (Subs 3.3, 3.4) lands DATA_EDIT and HITL resume
migrations on top of this. Until then this package is dormant when no
handler emits ``OutboxRequested`` — the dispatcher polls an empty
list and goes back to sleep.
"""

from app.layer4_frameworks.outbox.outbox_dispatcher import OutboxDispatcher
from app.layer4_frameworks.outbox.outbox_errors import (
    OutboxError,
    PermanentError,
    RetryableError,
)

__all__ = [
    "OutboxDispatcher",
    "OutboxError",
    "PermanentError",
    "RetryableError",
]
