from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from agent_sdk.layer1_domain.entities.agent_capability import AgentCapability
from agent_sdk.layer1_domain.entities.queue_metadata import QueueMetadata
from agent_sdk.layer1_domain.exceptions import RegistrationError
from agent_sdk.layer2_application.interfaces.agent_registry import IAgentRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

ToolFactory = Callable[[AgentCapability, IAgentRegistry], Any]


class AgentDiscoveryService:
    """Discovers agents from the registry and generates capabilities + tools.

    Usage::

        from agent_sdk import AgentDiscoveryService, default_tool_factory

        service = AgentDiscoveryService(
            registry=registry,
            tool_factory=default_tool_factory,
        )
        capabilities, tools = await service.discover()

        # capabilities → pass to AgentCallCoordinator for validation/timeouts
        # tools → pass to ToolAgentBuilder or AgentGraphBuilder
    """

    def __init__(
        self,
        registry: IAgentRegistry,
        exclude_agent_types: list[str] | None = None,
        tool_factory: ToolFactory | None = None,
    ) -> None:
        self._registry = registry
        self._exclude = set(exclude_agent_types or [])
        self._tool_factory = tool_factory

    @staticmethod
    def _coerce_queue_metadata(data: dict[str, Any] | None) -> QueueMetadata | None:
        if not data:
            return None
        queue_name = data.get("queue_name") or ""
        request_topic = data.get("request_topic") or ""
        if not queue_name and not request_topic:
            return None
        delivery_hints = data.get("delivery_hints") or {}
        if not isinstance(delivery_hints, dict):
            delivery_hints = {}
        request_message_type = (
            data.get("request_message_type")
            or data.get("request_type")
            or data.get("message_type")
            or delivery_hints.get("request_message_type")
            or delivery_hints.get("request_type")
            or delivery_hints.get("message_type")
            or ""
        )
        response_message_type = (
            data.get("response_message_type")
            or data.get("response_type")
            or delivery_hints.get("response_message_type")
            or delivery_hints.get("response_type")
            or ""
        )
        return QueueMetadata(
            queue_name=queue_name,
            request_topic=request_topic,
            reply_topic=data.get("reply_topic", ""),
            delivery_hints=delivery_hints,
            request_message_type=str(request_message_type),
            response_message_type=str(response_message_type),
        )

    @staticmethod
    def _prefer_routing_value(
        routing: dict[str, Any],
        agent_data: dict[str, Any],
        field_name: str,
        default: Any,
    ) -> Any:
        if routing.get(field_name) is not None:
            return routing[field_name]
        if agent_data.get(field_name) is not None:
            return agent_data[field_name]
        return default

    @classmethod
    def _build_capability(cls, agent_data: dict[str, Any]) -> AgentCapability | None:
        config = agent_data.get("configuration", {})
        metadata = agent_data.get("metadata") or {}
        agent_type = (
            metadata.get("agent_type")
            or agent_data.get("agent_type")
            or config.get("agent_type")
        )
        if not agent_type and (
            agent_data.get("id")
            or agent_data.get("invoke_endpoint")
            or agent_data.get("healthcheck_endpoint")
        ):
            agent_type = agent_data.get("name")
        if not agent_type:
            logger.warning(
                "Skipping agent %s — no agent_type in configuration",
                agent_data.get("id") or agent_data.get("agent_id"),
            )
            return None

        routing = (
            metadata.get("routing")
            or agent_data.get("routing")
            or config.get("routing", {})
        )
        queue_metadata = cls._coerce_queue_metadata(
            metadata.get("queue_metadata")
            or agent_data.get("queue_metadata")
            or config.get("queue_metadata")
        )
        return AgentCapability(
            agent_type=agent_type,
            name=agent_data.get("name", agent_type),
            description=agent_data.get("description", f"Agent: {agent_type}"),
            input_schema=cls._prefer_routing_value(
                routing,
                agent_data,
                "input_schema",
                {"type": "object", "properties": {}},
            ),
            output_schema=cls._prefer_routing_value(
                routing,
                agent_data,
                "output_schema",
                {},
            ),
            required_parameters=cls._prefer_routing_value(
                routing,
                agent_data,
                "required_parameters",
                [],
            ),
            negative_examples=cls._prefer_routing_value(
                routing,
                agent_data,
                "negative_examples",
                [],
            ),
            timeout_seconds=cls._prefer_routing_value(
                routing,
                agent_data,
                "timeout_seconds",
                300.0,
            ),
            required_confirmation=cls._prefer_routing_value(
                routing,
                agent_data,
                "required_confirmation",
                True,
            ),
            enabled=True,
            semantic_intents=(
                agent_data.get("semantic_intents")
                or metadata.get("semantic_intents")
                or []
            ),
            queue_metadata=queue_metadata,
        )

    async def discover(
        self,
        domain: str | None = None,
    ) -> tuple[list[AgentCapability], list[Any]]:
        raw_agents: list[dict[str, Any]] = []
        capability_lister = getattr(self._registry, "list_capabilities", None)
        if capability_lister is not None:
            try:
                listed_capabilities = await capability_lister()
                if listed_capabilities:
                    raw_agents = listed_capabilities
            except (RegistrationError, TypeError) as exc:
                logger.warning(
                    "Capability listing failed: %s. Falling back to active agents.",
                    exc,
                )
                raw_agents = []

        capabilities: list[AgentCapability] = []
        tools: list[Any] = []

        if not raw_agents:
            raw_agents = await self._registry.list_active_agents(domain=domain)

        for agent_data in raw_agents:
            cap = self._build_capability(agent_data)
            if cap is None:
                continue
            if cap.agent_type in self._exclude:
                continue
            capabilities.append(cap)

            if self._tool_factory is not None:
                tool = self._tool_factory(cap, self._registry)
                tools.append(tool)

        logger.info(
            "Discovered %d agents: %s",
            len(capabilities),
            [c.agent_type for c in capabilities],
        )
        return capabilities, tools
