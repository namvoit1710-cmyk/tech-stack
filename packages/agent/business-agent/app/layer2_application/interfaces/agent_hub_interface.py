from abc import ABC, abstractmethod
from typing import Optional
from app.layer1_domain.entities.agent_hub import AgentHub, ToolHub, WorkflowHub


class IAgentHub(ABC):

    @abstractmethod
    async def get(self, agent_id: str) -> AgentHub: ...

    @abstractmethod
    async def get_tools_by_ids(self, ids: list[str]) -> list[ToolHub]: ...

    @abstractmethod
    async def get_tools_by_id(self, tool_id : str) -> Optional[ToolHub]:...

    @abstractmethod
    async def get_workflows_by_ids(self, ids: list[str]) -> list[WorkflowHub]: ...

    @abstractmethod
    async def get_agents_by_ids(self, ids: list[str]) -> list[AgentHub]: ...
