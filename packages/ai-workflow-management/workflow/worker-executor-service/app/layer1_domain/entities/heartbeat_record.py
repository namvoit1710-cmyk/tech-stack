from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class HeartbeatRecord:
    """Periodic health signal from a worker."""

    worker_id: str = ""
    task_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    healthy: bool = True
    progress: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
