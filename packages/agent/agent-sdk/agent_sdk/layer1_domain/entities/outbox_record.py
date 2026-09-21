from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: str
    topic: str
    message: dict[str, Any]
    key: str = ""
    status: str = "PENDING"  # "PENDING", "PUBLISHED", "FAILED"
    created_at: str = ""
    published_at: str = ""
    attempts: int = 0
