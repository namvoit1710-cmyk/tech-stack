from dataclasses import dataclass, field
from typing import Any


@dataclass
class EventEnvelope:
    type: str = ""
    message_id: str = ""
    correlation_id: str = ""
    conversation_id: str = ""
    payload: Any = field(default_factory=dict)
