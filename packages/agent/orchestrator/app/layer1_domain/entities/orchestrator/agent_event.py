from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.entities.orchestrator.ui_event import UIEventRole


@dataclass(slots=True)
class AgentEvent:
    event_id: str = ""
    role: UIEventRole = UIEventRole.ASSISTANT
    event_type: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = ""
    conv_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    
