"""
HTTP Capability Resolver.

Calls the shared registry API to resolve an execute_by value into a
ResolvedCapability record (LOCAL_TOOL or TECHNICAL_AGENT).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import httpx

from app.layer1_domain.entities.step import ExecutionTarget, ResolvedCapability
from app.layer1_domain.interfaces.capability_resolver_interface import (
    ICapabilityResolver,
)

logger = logging.getLogger(__name__)

ExecuteByType = Literal["tool", "agent"]
EXECUTE_BY_PATTERN = re.compile(
    r"^(?P<execute_type>tool|agent)\."
    r"(?P<execute_id>"
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
    r")$"
)


class ToolMetadataModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tool_name: str | None = None


class ToolRegistryItemModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    status: str
    metadata: ToolMetadataModel | None = None


class ToolsByIdsResponseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    tools: list[ToolRegistryItemModel] | None = None


class QueueMetadataModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    queue_name: str | None = None
    request_topic: str | None = None
    reply_topic: str | None = None
    delivery_hints: dict[str, Any] = Field(default_factory=dict)
    request_message_type: str | None = None
    response_message_type: str | None = None
    request_topics: list[str] = Field(default_factory=list)
    queue_names: list[str] = Field(default_factory=list)


class AgentRuntimeConfigModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    system_prompt: str | None = None
    max_concurrency: int | None = None
    llm_model: str | None = None
    llm_provider: str | None = None
    llm_temperature: float | None = None
    llm_timeout: int | float | None = None
    llm_max_tokens: int | None = None
    llm_max_retries: int | None = None


class RoutingModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    negative_examples: list[str] = Field(default_factory=list)
    timeout_seconds: int | float | None = None


class AgentMetadataModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    description: str | None = None
    agent_type: str | None = None
    sdk_version: str | None = None
    agent_domain: str | None = None
    endpoint_url: str | None = None
    sdk_kind: str | None = None
    routing: RoutingModel | None = None
    agent_runtime_config: AgentRuntimeConfigModel | None = None
    queue_metadata: QueueMetadataModel | None = None
    logical_registry_name: str | None = None
    definition_hash: str | None = None
    definition_hash_algorithm: str | None = None


class AgentConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    agent_type: str | None = None
    routing: RoutingModel | None = None
    queue_metadata: QueueMetadataModel | None = None


class AgentRegistryItemModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    kind: str
    status: str
    is_published: bool
    version: str | None = None
    config_type: str | None = None
    is_alive: bool | None = None
    healthcheck_endpoint: str | None = None
    invoke_endpoint: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    workflows: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    knowledge_base: list[str] = Field(default_factory=list)
    description: str | None = None
    business: str | None = None
    last_health_check_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    metadata: AgentMetadataModel | None = None
    configuration: AgentConfigurationModel | None = None


class HttpCapabilityResolver(ICapabilityResolver):
    """
    Resolves capabilities by calling the Agent Registry Service API.

    Resolves execute_by as either agent.<agent_id> or tool.<tool_id> using the
    documented registry endpoints.
    """

    def __init__(self, registry_url: str) -> None:
        self._registry_url = registry_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=10.0)
        # In-memory cache to avoid repeated registry calls within a batch
        self._cache: dict[str, ResolvedCapability | None] = {}

    async def resolve(self, execute_by: str) -> ResolvedCapability | None:
        """
        Resolve execute_by to a capability record.

        Returns None if the capability is not found in the registry.
        """
        if not execute_by:
            return None

        # Check cache first
        if execute_by in self._cache:
            return self._cache[execute_by]

        capability = await self._fetch_from_registry(execute_by)
        self._cache[execute_by] = capability
        return capability

    async def _fetch_from_registry(self, execute_by: str) -> ResolvedCapability | None:
        """Fetch capability metadata from the registry."""
        if not self._registry_url:
            logger.warning(
                "REGISTRY_URL is not configured. Cannot resolve execute_by='%s'.",
                execute_by,
            )
            return None

        target = self._parse_execute_by(execute_by)
        if target is None:
            logger.warning(
                "Unsupported execute_by format '%s'. Expected tool.<tool_id> or agent.<agent_id>.",
                execute_by,
            )
            return None

        target_type, capability_id = target

        try:
            if target_type == "agent":
                agent = await self._fetch_agent_by_id(capability_id)
                if agent is not None:
                    return self._to_agent_capability(agent, execute_by)

            if target_type == "tool":
                tool = await self._fetch_tool_by_id(capability_id)
                if tool is not None:
                    return self._to_tool_capability(tool, execute_by)

            logger.info("Capability '%s' not found in registry", execute_by)
            return None

        except httpx.HTTPError as exc:
            logger.warning(
                "Registry HTTP call failed for '%s': %s",
                execute_by,
                str(exc),
            )
            return None

    def _parse_execute_by(self, execute_by: str) -> tuple[ExecuteByType, str] | None:
        match = EXECUTE_BY_PATTERN.fullmatch(execute_by)
        if not match:
            return None

        execute_type = match.group("execute_type")
        execute_id = match.group("execute_id").lower()

        return execute_type, execute_id

    async def _fetch_agent_by_id(self, agent_id: str) -> AgentRegistryItemModel | None:
        response = await self._client.get(
            f"{self._registry_url}/api/v1/agents/{agent_id}"
        )
        if response.status_code == 404:
            return None

        response.raise_for_status()
        try:
            return AgentRegistryItemModel.model_validate(response.json())
        except ValidationError as exc:
            response_text = getattr(response, "text", "<unavailable>")
            logger.warning(
                "Malformed agent payload for id '%s': %s. Validation error: %s",
                agent_id,
                response_text,
                str(exc),
            )
            return None

    async def _fetch_tool_by_id(self, tool_id: str) -> ToolRegistryItemModel | None:
        response = await self._client.post(
            f"{self._registry_url}/api/v1/tools/by_ids",
            json=[tool_id],
        )
        if response.status_code == 404:
            return None

        response.raise_for_status()

        try:
            payload = ToolsByIdsResponseModel.model_validate(response.json())
        except ValidationError as exc:
            response_text = getattr(response, "text", "<unavailable>")
            logger.warning(
                "Malformed tools by ids payload for id '%s': %s. Validation error: %s",
                tool_id,
                response_text,
                str(exc),
            )
            return None

        if not payload.tools:
            return None

        for tool in payload.tools:
            if tool.id == tool_id:
                return tool

        return None

    def _to_agent_capability(
        self, agent: AgentRegistryItemModel, execute_by: str
    ) -> ResolvedCapability | None:
        kind = str(agent.kind or "").lower()
        if kind != "technical":
            logger.warning(
                "Agent id '%s' resolved to unsupported kind '%s'",
                execute_by,
                kind or "<empty>",
            )
            return None

        return ResolvedCapability(
            capability_id=str(agent.id or execute_by),
            execution_target=ExecutionTarget.TECHNICAL_AGENT,
            agent_type=self._extract_agent_type(agent),
            queue_metadata=self._extract_queue_metadata(agent),
            enabled=self._is_agent_enabled(agent),
        )

    def _to_tool_capability(
        self, tool: ToolRegistryItemModel, execute_by: str
    ) -> ResolvedCapability | None:
        tool_name = self._extract_local_tool_name(tool)
        if not isinstance(tool_name, str) or not tool_name:
            logger.warning(
                "Tool id '%s' resolved without a usable name: %s",
                execute_by,
                tool,
            )
            return None

        return ResolvedCapability(
            capability_id=str(tool.id or execute_by),
            execution_target=ExecutionTarget.LOCAL_TOOL,
            tool_name=tool_name,
            enabled=self._is_tool_enabled(tool),
        )

    def _extract_agent_type(self, agent: AgentRegistryItemModel) -> str:
        metadata = agent.metadata
        if metadata is not None:
            agent_type = metadata.agent_type
            if isinstance(agent_type, str) and agent_type:
                return agent_type

            logical_name = metadata.logical_registry_name
            if isinstance(logical_name, str) and logical_name:
                return logical_name

        configuration = agent.configuration
        if configuration is not None:
            agent_type = configuration.agent_type
            if isinstance(agent_type, str) and agent_type:
                return agent_type

        return ""

    def _extract_local_tool_name(self, tool: ToolRegistryItemModel) -> str | None:
        metadata = tool.metadata
        if metadata is not None:
            local_tool_name = metadata.tool_name
            if isinstance(local_tool_name, str) and local_tool_name:
                return local_tool_name

        tool_name = tool.name
        if isinstance(tool_name, str) and tool_name:
            return tool_name

        return None

    def _extract_queue_metadata(self, agent: AgentRegistryItemModel) -> dict[str, Any]:
        metadata = agent.metadata
        if metadata is not None and metadata.queue_metadata is not None:
            return metadata.queue_metadata.model_dump(exclude_none=True)

        configuration = agent.configuration
        if configuration is not None and configuration.queue_metadata is not None:
            return configuration.queue_metadata.model_dump(exclude_none=True)

        return {}

    def _is_agent_enabled(self, agent: AgentRegistryItemModel) -> bool:
        status = str(agent.status or "").lower()
        is_published = agent.is_published is True
        is_alive = agent.is_alive is True if agent.is_alive is not None else None
        return status == "active" and is_published and (is_alive is None or is_alive is True)

    def _is_tool_enabled(self, tool: ToolRegistryItemModel) -> bool:
        return str(tool.status or "").lower() == "active"

    async def close(self) -> None:
        await self._client.aclose()
