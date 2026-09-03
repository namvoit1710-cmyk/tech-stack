from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class InterruptType(str, Enum):
    CONFIRMATION = "CONFIRMATION"
    DATA_REQUEST = "DATA_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_REQUEST = "PERMISSION_REQUEST"
    AGENT_CALL = "AGENT_CALL"
    GENERIC = "GENERIC"


@dataclass
class HitlInterruptPayload:
    thread_id: str
    interrupt_id: str
    value: Any
    type: InterruptType = InterruptType.GENERIC
    message: str = ""
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    conv_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
