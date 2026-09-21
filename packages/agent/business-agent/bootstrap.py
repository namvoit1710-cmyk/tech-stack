from agent_sdk import (
    AgentGraphBuilder,
    build_app_container as _sdk_build_app_container,
    create_checkpointer,
)
from app.layer2_application.features.main_agent.use_cases import (
    load_features,
    load_all_usecases,
)
from app.layer4_frameworks.config.app_config import settings
from app.layer4_frameworks.graph.graph_builder import (
    build_graph,
)
from app.layer4_frameworks.dependency.ioc_container import build_dependencies
from app.layer4_frameworks.dependency.ioc_container import build_tools
from app.layer4_frameworks.planners.planner_resolver import PlannerResolver
from app.layer4_frameworks.reviewer.reviewer_resolver import ReviewerResolver
from app.layer4_frameworks.messaging.push_gateway_notifier import PushGatewayNotifier
from app.layer4_frameworks.messaging.push_gateway_usage_recorder import (
    wire_llm_usage_recorder,
)
from app.layer4_frameworks.repositories.hana_business_agent_shared_state_repository import (
    HanaBusinessAgentSharedStateRepository,
)
from app.layer4_frameworks.repositories.hana_ui_event_repository import HanaUIEventRepository
from app.layer4_frameworks.repositories.hana_chat_timeline_node_repository import HanaChatTimelineNodeRepository
from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import (
    HanaAgentSharedStateRepository,
)
from agent_sdk.layer1_domain.value_objects.agent_control import (
    is_agent_control_message,
)
from agent_sdk.layer2_application.services.message_type_settings import (
    configured_response_message_types,
)
from typing import Any
import logging
logging.basicConfig(level=logging.INFO)
from app.layer4_frameworks.planners.workflow_input_node import WorkflowInputNode
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
        local_tools=build_tools(),
    )

    deps = container["_dependencies"]

    _wrap_consumer_start(container.get("consumer"))

    shared_state_repository = deps.get("agent_shared_state_repository")
    if isinstance(shared_state_repository, HanaAgentSharedStateRepository) and not isinstance(
        shared_state_repository, HanaBusinessAgentSharedStateRepository
    ):
        deps["agent_shared_state_repository"] = HanaBusinessAgentSharedStateRepository(
            repo=shared_state_repository._repo
        )

    hana_connection_manager = deps.get("hana_connection_manager")
    deps["ui_event_repository"] = HanaUIEventRepository(hana_connection_manager)
    deps["chat_timeline_node_repository"] = HanaChatTimelineNodeRepository(hana_connection_manager)

    llm = deps.get("llm_service")
    # Create planner_resolver using SDK's llm (LangChain ChatModel)
    deps["planner_resolver"] = PlannerResolver(llm=llm)

    # Create reviewer_resolver using SDK's llm (LangChain ChatModel)
    deps["reviewer_resolver"] = ReviewerResolver(llm=llm)

    # Wrap SDK's push_gateway_notifier with business-specific methods
    deps["push_gateway_notifier"] = PushGatewayNotifier(
        notifier=deps["push_gateway_notifier"],
        logger=deps["logger"],
        uuid_builder=deps["uuid_builder"],
        ui_event_repository=deps["ui_event_repository"],
        chat_timeline_node_repository=deps["chat_timeline_node_repository"],
    )

    # SA-2079: fan out every LLM usage record to the existing recorder (if enabled) and to a
    # new one that emits a token_info push-gateway event for the active turn.
    if llm is not None:
        wire_llm_usage_recorder(llm, deps["push_gateway_notifier"])

    deps["workflow_input_node"] = WorkflowInputNode(llm=llm)

    load_features(deps["logger"])
    load_all_usecases(deps, deps["logger"])

    return container
