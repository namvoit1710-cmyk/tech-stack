from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class HitlResumeCommand:
    thread_id: str
    resume_value: Any
    interrupt_id: Optional[str] = None
