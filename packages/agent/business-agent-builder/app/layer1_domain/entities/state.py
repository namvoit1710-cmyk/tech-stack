from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState

from app.layer1_domain.entities.business_agent_draft import BusinessAgentDraft


class MainState(AgentBaseState):
    # Natural-language request from the Agent Hub chat box.
    prompt: str
    # The business-agent configuration drafted from the prompt.
    draft: BusinessAgentDraft
