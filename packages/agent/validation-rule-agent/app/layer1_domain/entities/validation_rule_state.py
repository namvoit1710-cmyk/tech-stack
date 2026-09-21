from __future__ import annotations

from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState


class ValidationRuleState(AgentBaseState, total=False):
    agent_result: dict[str, object] | None
