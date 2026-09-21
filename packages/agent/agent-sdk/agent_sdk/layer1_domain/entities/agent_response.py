from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class AgentResponse:
    message: str
    status: str = "success"
    conv_id: str = ""
    session_id: str = ""
    agent: str = ""
    agent_data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: Optional[str] = None
    correlation_id: Optional[str] = None
