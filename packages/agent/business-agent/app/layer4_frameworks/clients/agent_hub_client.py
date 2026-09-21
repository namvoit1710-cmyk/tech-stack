import httpx
from typing import Optional
from app.layer2_application.interfaces.agent_hub_interface import IAgentHub
from app.layer1_domain.entities.agent_hub import AgentHub, ToolHub, ToolHubResponse, WorkflowHub, WorkflowHubResponse
from app.layer2_application.interfaces.logger_interface import ILogger


class AgentHubClient(IAgentHub):
    def __init__(self, base_url: str, logger: ILogger) -> None:
        self._base_url = base_url.rstrip("/")
        self._logger = logger

    async def get(self, agent_id: str) -> Optional[AgentHub]:
        if not self._base_url:
            self._logger.info(
                f"Agent Hub API URL is not configured. Cannot fetch agent info for {agent_id}"
            )
            return None

        url = f"{self._base_url}/api/v1/agents/{agent_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

                data = response.json()
                return AgentHub.from_dict(data)

        except Exception as exc:
            self._logger.info(f"Failed to fetch agent info for {agent_id}: {exc}")
            return None


    async def get_tools_by_ids(self, ids: list[str]) -> list[ToolHub]:
        if not self._base_url:
            self._logger.info(
                "Agent Hub API URL is not configured. Cannot fetch tool info",
                ids=ids,
            )
            return []

        url = f"{self._base_url}/api/v1/tools/by_ids"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=ids)
                response.raise_for_status()

                data = response.json()
                result = ToolHubResponse.from_dict(data)

                return result.tools

        except Exception as exc:
            self._logger.info(
                "Failed to fetch tool info",
                ids=ids,
                error=str(exc),
            )
            return []
        
    async def get_tools_by_id(self, tool_id : str) -> Optional[ToolHub]:
        if not self._base_url:
            self._logger.info(
                "Agent Hub API URL is not configured. Cannot fetch tool info",
                tool_id=tool_id,
            )
            return None

        url = f"{self._base_url}/api/v1/tools/{tool_id}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()

                data = response.json()
                result = ToolHub.from_dict(data)

                return result

        except Exception as exc:
            self._logger.info(
                "Failed to fetch tool info",
                tool_id=tool_id,
                error=str(exc),
            )
            return None
        
    async def get_workflows_by_ids(self, ids: list[str]) -> list[WorkflowHub]:
        if not self._base_url:
            self._logger.info(
                "Agent Hub API URL is not configured. Cannot fetch workflow info",
                ids=ids,
            )
            return []

        url = f"{self._base_url}/api/v1/workflows/by_ids"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=ids)
                response.raise_for_status()

                data = response.json()
                result = WorkflowHubResponse.from_dict(data)

                return result.workflows

        except Exception as exc:
            self._logger.info(
                "Failed to fetch workflow info",
                ids=ids,
                error=str(exc),
            )
            return []
        
    async def get_agents_by_ids(self, ids: list[str]) -> list[AgentHub]:
        if not self._base_url:
            self._logger.info(
                "Agent Hub API URL is not configured. Cannot fetch agent info",
                ids=ids,
            )
            return []

        url = f"{self._base_url}/api/v1/agents/by_ids"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, json=ids)
                response.raise_for_status()

                data = response.json()
                items = data.get("agents", data) if isinstance(data, dict) else data

                return [
                    AgentHub.from_dict(item)
                    for item in (items or [])
                    if isinstance(item, dict)
                ]

        except Exception as exc:
            self._logger.info(
                "Failed to fetch agent info",
                ids=ids,
                error=str(exc),
            )
            return []