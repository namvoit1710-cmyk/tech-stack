from typing import Annotated

from langgraph.graph.message import add_messages

from agent_sdk.layer1_domain.entities.agent_state import AgentBaseState


class ToolAgentState(AgentBaseState, total=False):
    messages: Annotated[list, add_messages]
    tool_results: list[dict]
