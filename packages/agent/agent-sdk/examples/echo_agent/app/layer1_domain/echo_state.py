from typing import Any, Dict

from agent_sdk import AgentBaseState


class EchoState(AgentBaseState, total=False):
    agent_result: Dict[str, Any]
    confirmed: bool
