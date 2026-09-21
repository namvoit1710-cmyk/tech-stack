from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InboxRecord:
    message_id: str
    idempotency_key: str
    payload: dict
    status: str  # "RECEIVED", "PROCESSING", "COMPLETED", "FAILED"
    received_at: str
    processed_at: str = ""
    error: str = ""
