from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AgentInfo:
    agent_type: str
    version: str
    sdk_version: str
    domain: str
    capabilities: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
