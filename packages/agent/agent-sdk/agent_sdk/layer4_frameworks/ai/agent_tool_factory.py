"""Default tool factory for AgentDiscoveryService.

Provides the default_tool_factory function that creates RemoteAgentTool
instances. This lives in layer4 so AgentDiscoveryService (layer2) stays
clean of framework imports — the architecture boundary tests (ast.walk)
forbid layer2 from importing layer4, even lazily.
"""

from __future__ import annotations

from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability
from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry
from agent_sdk.layer4_frameworks.ai.remote_agent_tool import RemoteAgentTool


def default_tool_factory(
    capability: AgentCapability,
    registry: IAgentRegistry,
) -> RemoteAgentTool:
    """Create a RemoteAgentTool from an AgentCapability.

    Enriches the tool description with negative_examples for better
    LLM routing accuracy.
    """
    desc = capability.description
    if capability.negative_examples:
        desc += "\n\nDo NOT call this agent when:\n"
        desc += "\n".join(f"- {ex}" for ex in capability.negative_examples)

    tool_name = f"call_{capability.agent_type.replace('-', '_')}"
    return RemoteAgentTool(
        remote_agent_type=capability.agent_type,
        registry=registry,
        name=tool_name,
        description=desc,
    )
