from typing import Optional

from app.layer1_domain.echo_state import EchoState
from app.layer2_application.echo_nodes import hitl_node, openai_node
from langgraph.graph import END

from agent_sdk import AgentGraphBuilder


def build_echo_graph(deps: Optional[dict] = None, checkpointer=None):
    deps = deps or {}
    builder = AgentGraphBuilder(deps=deps, state_schema=EchoState)
    builder.add_node("openai", openai_node)
    builder.add_node("hitl", hitl_node)
    builder.set_entry_point("openai")
    # builder.add_edge("openai", "hitl")
    builder.add_edge("openai", END)
    return builder.compile(checkpointer=checkpointer)
