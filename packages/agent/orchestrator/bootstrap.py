import logging
from typing import Any

from agent_sdk import (
    AgentGraphBuilder,
    build_app_container as _sdk_build_app_container,
    create_checkpointer,
)
from agent_sdk.layer1_domain.value_objects.agent_control import (
    is_agent_control_message,
)
from agent_sdk.layer2_application.services.message_type_settings import (
    configured_response_message_types,
)
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.graph.graph_builder import (
    build_graph,
)
from app.layer4_frameworks.dependency.ioc_container import build_dependencies
from app.layer4_frameworks.stop_targets import build_default_stop_targets
from app.layer2_application.features import load_all_usecases
from app.layer4_frameworks.repositories.agent_event_repository import HanaAgentEventRepository
from app.layer4_frameworks.ai.instrumented_llm_provider import InstrumentedLLMProvider
from app.layer4_frameworks.messaging.push_gateway_notifier import PersistingPushGatewayNotifier
from app.layer4_frameworks.repositories.conversation_summary_repository import HanaConversationSummaryRepository
from app.layer4_frameworks.repositories.user_profile_repository import HanaUserProfileRepository
from app.layer4_frameworks.repositories.hana_agent_memory_repository import HanaAgentMemoryRepository
from app.layer2_application.features.agent_memory.agent_memory_policy import AgentMemoryService
from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import BaseHanaRepository, HanaAgentSharedStateRepository
from app.layer4_frameworks.persistence.hana.migrations import migrate

logger = logging.getLogger(__name__)


class _WrappedDelivery:
    def __init__(self, delivery: Any, payload: dict[str, Any]) -> None:
        self._delivery = delivery
        self.payload = payload

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delivery, name)


def _wrap_inbound_payload(raw_payload: Any) -> dict[str, Any]:
    logging.info("raw_payload=%s", raw_payload)
    return {
        "parameters": {
            "event": raw_payload,
        },
    }


def _wrap_consumer_start(consumer: Any) -> None:
    if consumer is None or not hasattr(consumer, "start"):
        return

    original_start = consumer.start
    # Message types that must reach the consumer intake UNWRAPPED (they key off
    # the top-level ``type``): agent.control -> cancel path; agent.response (+ any
    # configured response type) -> resume path. Everything else is an execute
    # message and keeps the parameters.event wrap the graph expects.
    response_types = configured_response_message_types(settings)

    async def _wrapped_start(handler):
        async def _wrapped_handler(delivery):
            raw_payload = getattr(delivery, "payload", delivery)
            top_type = (
                raw_payload.get("type") if isinstance(raw_payload, dict) else None
            )
            # SA-2042 (+ agent.response sibling): control-plane (Stop) and response
            # messages must NOT be wrapped. Wrapping hides the top-level ``type``, so
            # the consumer misroutes the message into the graph ("... is not a valid
            # QueueRequestType") instead of its cancel (control) / resume (response)
            # intake. Pass them through untouched.
            if is_agent_control_message(raw_payload) or top_type in response_types:
                await handler(delivery)
                return
            await handler(
                _WrappedDelivery(delivery, _wrap_inbound_payload(raw_payload))
            )

        return await original_start(_wrapped_handler)

    consumer.start = _wrapped_start


def build_app_container() -> dict:
    checkpointer = create_checkpointer(settings)
    graph_deps = build_dependencies()

    container = _sdk_build_app_container(
        agent_graph=build_graph(
            deps=graph_deps,
            checkpointer=checkpointer,
            builder_factory=AgentGraphBuilder,
        ),
        extra_dependencies={
            "settings": settings,
            **graph_deps,
        },
    )

    dependencies = container["_dependencies"]

    _wrap_consumer_start(container.get("consumer"))

    hana_connection_manager = dependencies["hana_connection_manager"]
    if hasattr(hana_connection_manager, "execute_write"):
        migrate(hana_connection_manager)
    dependencies["user_profile_repo"] = HanaUserProfileRepository(
        hana_connection_manager
    )
    dependencies["agent_event_repo"] = HanaAgentEventRepository(hana_connection_manager)
    dependencies["conversation_summary_repo"] = HanaConversationSummaryRepository(hana_connection_manager)

    # Wrap the raw push-gateway notifier so every orchestrator-originated reply is persisted
    # before it is pushed (mirrors the business-agent notifier). Keeps the raw protocol
    # passthrough, so it stays a drop-in for any dependency expecting the notifier.
    raw_push_gateway_notifier = dependencies.get("push_gateway_notifier")
    if raw_push_gateway_notifier is not None:
        dependencies["push_gateway_notifier"] = PersistingPushGatewayNotifier(
            notifier=raw_push_gateway_notifier,
            agent_event_repo=dependencies["agent_event_repo"],
            uuid_builder=dependencies.get("uuid_builder"),
            logger=dependencies["logger"],
        )
    # SA-2079: wrap the LLM provider so every complete_with_format() call emits a token_info
    # event for the active turn (TurnMeter, set in OrchestrateGraphUseCase.execute).
    raw_llm_provider = dependencies.get("llm_provider")
    if raw_llm_provider is not None and dependencies.get("push_gateway_notifier") is not None:
        dependencies["llm_provider"] = InstrumentedLLMProvider(
            inner=raw_llm_provider,
            notifier=dependencies["push_gateway_notifier"],
        )

    shared_state_repository = dependencies.get("agent_shared_state_repository")
    dependencies["orchestration_state_repo"] = shared_state_repository

    # SA-1436: orchestrator-owned long-term memory (policy + storage + recall + audit).
    dependencies["agent_memory_repository"] = HanaAgentMemoryRepository(
        hana_connection_manager
    )
    dependencies["agent_memory_service"] = AgentMemoryService(
        repository=dependencies["agent_memory_repository"],
        llm_provider=dependencies.get("llm_provider"),
        uuid_builder=dependencies.get("uuid_builder"),
        logger=dependencies["logger"],
        settings=settings,
    )

    # SA-892 (Ports & Adapters): build the stop fan-out targets at the composition
    # root and inject them (by param name) into StopConversationUseCase, so the
    # layer2 use case stays free of any concrete adapter dependency.
    dependencies["stop_targets"] = build_default_stop_targets(
        publisher=dependencies.get("publisher"),
        settings=settings,
        logger=dependencies["logger"],
        agent_registry_client=dependencies.get("agent_registry_client"),
        uuid_builder=dependencies.get("uuid_builder"),
        conversation_run_index=dependencies.get("conversation_run_index"),
        orchestration_state_repo=dependencies.get("orchestration_state_repo"),
    )

    usecases = load_all_usecases(dependencies, logger=dependencies["logger"], reload=True)
    container["_usecases"] = usecases

    return container
