from typing import Protocol
from abc import ABC, abstractmethod
from app.layer1_domain.entities.agent.scored_agent import ScoredAgent, AgentKind, AgentStatus


class IAgentRegistryClient(ABC):
    """Port for Layer 4 to communicate with Layer 3 Agent Registry."""

    @abstractmethod
    async def filter_agents(
        self,
        *,
        status: AgentStatus | None = None,
        kind: AgentKind | None = None,
        is_published: bool | None = None,
    ) -> list[ScoredAgent]: ...

    @abstractmethod
    async def get_available_agents(self) -> list[ScoredAgent]: ...

    @abstractmethod
    async def get_tool_id_by_name(self, name: str) -> str | None:
        """Resolve a registered tool's id from its (unique) name.

        SA-892: the stop path discovers the ``workflow_stop`` tool's registry id at
        runtime (rather than pinning ``tool.<uuid>`` in deployment config), the same
        way ``workflow_start_run``'s ``tool.<uuid>`` is discovered from the registry
        catalog at plan time. Returns the bare tool id (the caller builds
        ``tool.<id>``), or ``None`` when unknown / the registry is unreachable.
        """
        ...
