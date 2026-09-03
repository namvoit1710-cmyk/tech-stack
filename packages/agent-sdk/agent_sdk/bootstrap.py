import asyncio
import importlib
import inspect
import logging
import os
import pkgutil
import time
from collections.abc import Awaitable, Callable
from typing import Any, Dict, cast

from agent_sdk.layer2_application.features.execute_agent.use_cases.resume_agent_use_case import (
    ResumeAgentUseCase,
)
from agent_sdk.layer2_application.interfaces.llm_service import ILLMService
from agent_sdk.layer4_frameworks.config.app_config import settings
from agent_sdk.layer4_frameworks.logging.standard import StandardLogger
from agent_sdk.layer4_frameworks.mcp.mcp_client_service import MCPClientService
from agent_sdk.layer4_frameworks.monitoring.prometheus import PrometheusMonitor
from agent_sdk.layer4_frameworks.registry.http_agent_registry import HttpAgentRegistry
from agent_sdk.layer4_frameworks.registry.http_tool_registry import HttpToolRegistry
from agent_sdk.layer4_frameworks.registry.tool_registrar import ToolRegistrar

logger = logging.getLogger(__name__)
_MCP_TOOL_LOAD_MAX_ATTEMPTS = 3
_MCP_TOOL_LOAD_INITIAL_BACKOFF_SECONDS = 1.0


def _read_string_setting(settings: Any, name: str, *, default: str = "") -> str:
    value = getattr(settings, name, default)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _read_bool_setting(settings: Any, name: str, *, default: bool = False) -> bool:
    value = getattr(settings, name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, int):
        return bool(value)
    return default


def _should_create_llm_service(settings: Any) -> bool:
    return _read_bool_setting(settings, "LLM_USE_LITELLM_PROXY") or bool(
        _read_string_setting(settings, "LLM_MODEL")
    )


def _resolve_infra_mode(settings: Any) -> str:
    return (
        _read_string_setting(settings, "MESSAGING_MODE")
        or _read_string_setting(settings, "INFRA_MODE", default="mock")
    ).lower()


_EVENT_MESH_PROTOCOL_ALIASES: dict[str, tuple[str, ...]] = {
    "rest": ("httprest",),
    "http": ("httprest",),
    "https": ("httprest",),
    "httprest": ("httprest",),
    "amqp": ("amqp10ws", "amqp10"),
    "amqp10": ("amqp10",),
    "amqp10ws": ("amqp10ws",),
    "mqtt": ("mqtt311ws", "mqtt311"),
    "mqtt311": ("mqtt311",),
    "mqtt311ws": ("mqtt311ws",),
}


def _normalize_event_mesh_protocol(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "")


def _expand_event_mesh_protocol_preferences(
    value: object,
    *,
    default: str = "httprest",
) -> list[str]:
    raw_values = [value] if value not in (None, "") else [default]
    expanded: list[str] = []
    for raw in raw_values:
        normalized = _normalize_event_mesh_protocol(raw)
        for candidate in _EVENT_MESH_PROTOCOL_ALIASES.get(normalized, (normalized,)):
            candidate = _normalize_event_mesh_protocol(candidate)
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded


def _first_event_mesh_protocol(value: object, *, default: str = "httprest") -> str:
    preferences = _expand_event_mesh_protocol_preferences(value, default=default)
    return preferences[0] if preferences else default


def _resolve_event_mesh_protocol(settings: Any) -> str:
    value = _read_string_setting(
        settings, "EVENT_MESH_MESSAGING_PROTOCOL"
    ) or _read_string_setting(settings, "EVENT_MESH_PROTOCOL", default="httprest")
    return _first_event_mesh_protocol(value, default="httprest")


def _resolve_event_mesh_management_protocol(settings: Any) -> str:
    return _first_event_mesh_protocol(
        _read_string_setting(
            settings, "EVENT_MESH_MANAGEMENT_PROTOCOL", default="httprest"
        ),
        default="httprest",
    )


def _resolve_event_mesh_protocol_specific_url(
    settings: Any,
    prefix: str,
    protocol: str,
    *,
    generic_names: tuple[str, ...],
) -> str:
    for candidate in _expand_event_mesh_protocol_preferences(
        protocol, default=protocol
    ):
        value = _read_string_setting(settings, f"{prefix}_{candidate.upper()}_URL")
        if value:
            return value
    for name in generic_names:
        value = _read_string_setting(settings, name)
        if value:
            return value
    return ""


def _resolve_delegation_request_topic(settings: Any) -> str:
    for name in (
        "DELEGATION_REQUEST_TOPIC",
        "DELEGATION_COMPATIBILITY_REQUEST_TOPIC",
        "AGENT_REQUEST_COMPATIBILITY_TOPIC",
        "QUEUE_DELEGATION_REQUEST_TOPIC",
        "EVENT_MESH_AGENT_REQUEST_TOPIC",
        "KAFKA_AGENT_REQUEST_TOPIC",
        "QUEUE_REQUEST_TOPIC",
        "EVENT_MESH_REQUEST_TOPIC",
        "KAFKA_REQUEST_TOPIC",
    ):
        value = _read_string_setting(settings, name)
        if value:
            return value
    return "agent.request.agent"


def register_message_reaction_handlers(
    container: dict[str, Any],
    handlers: dict[str, Any] | None,
) -> None:
    normalized_handlers = dict(handlers or {})
    if not normalized_handlers:
        return

    existing_handlers = dict(container.get("message_reaction_handlers") or {})
    existing_handlers.update(normalized_handlers)
    container["message_reaction_handlers"] = existing_handlers

    dependencies = container.get("_dependencies")
    if isinstance(dependencies, dict):
        dependency_handlers = dict(dependencies.get("message_reaction_handlers") or {})
        dependency_handlers.update(normalized_handlers)
        dependencies["message_reaction_handlers"] = dependency_handlers

    router = container.get("message_reaction_router")
    if router is None:
        return

    register_many = getattr(router, "register_custom_handlers", None)
    if callable(register_many):
        register_many(normalized_handlers)
        return

    raise TypeError("router does not support register_custom_handlers")


class _AdaptedInboundDelivery:
    def __init__(self, delivery: Any, payload: Any) -> None:
        self._delivery = delivery
        self.payload = payload

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delivery, name)


def _wrap_consumer_start_with_adapter(
    consumer: Any,
    inbound_message_adapter: Callable[[Any], Any] | None,
    logger_instance: Any,
) -> None:
    if consumer is None or inbound_message_adapter is None:
        return
    if not hasattr(consumer, "start"):
        return
    if getattr(consumer, "_agent_sdk_inbound_message_adapter_wrapped", False):
        return

    original_start = consumer.start

    async def _adapted_start(handler):
        async def _adapted_handler(delivery):
            raw_payload = getattr(delivery, "payload", delivery)
            try:
                adapted_payload = inbound_message_adapter(raw_payload)
            except Exception:
                error = getattr(logger_instance, "error", None)
                if callable(error):
                    error("Inbound message adapter failed", exc_info=True)
                raise

            if adapted_payload is None:
                adapted_payload = raw_payload

            await handler(_AdaptedInboundDelivery(delivery, adapted_payload))

        return await original_start(_adapted_handler)

    consumer.start = _adapted_start
    setattr(consumer, "_agent_sdk_inbound_message_adapter_wrapped", True)


async def _await_result(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def _run_coroutine_sync(coro: Awaitable[Any]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result(coro))

    raise RuntimeError(
        "build_app_container() cannot be called from a running event loop when "
        "async dependencies must be resolved."
    )


def _run_loader_sync(loader: Callable[[], Awaitable[Any]]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_await_result(loader()))

    raise RuntimeError(
        "build_app_container() cannot be called from a running event loop when "
        "async dependencies must be resolved."
    )


def _snake_to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_"))


def _load_mcp_tools_with_retry(
    mcp_client: MCPClientService,
    tool_filter: list[str],
) -> list[Any]:
    delay_seconds = _MCP_TOOL_LOAD_INITIAL_BACKOFF_SECONDS

    for attempt in range(1, _MCP_TOOL_LOAD_MAX_ATTEMPTS + 1):
        loader = (
            (lambda: mcp_client.get_filtered_tools(tool_filter))
            if tool_filter
            else mcp_client.get_tools
        )
        try:
            mcp_tools = _run_loader_sync(loader)
        except RuntimeError:
            raise
        except Exception:
            if attempt == _MCP_TOOL_LOAD_MAX_ATTEMPTS:
                raise
            logger.warning(
                "MCP tool load attempt %d/%d failed; retrying in %.1fs",
                attempt,
                _MCP_TOOL_LOAD_MAX_ATTEMPTS,
                delay_seconds,
                exc_info=True,
            )
            time.sleep(delay_seconds)
            delay_seconds *= 2
            continue

        discovery_failures = list(getattr(mcp_client, "last_discovery_failures", []))
        if not discovery_failures or attempt == _MCP_TOOL_LOAD_MAX_ATTEMPTS:
            if discovery_failures:
                logger.warning(
                    "Proceeding with partial MCP tools after %d attempts; failed servers: %s",
                    attempt,
                    ", ".join(discovery_failures),
                )
            return mcp_tools

        logger.warning(
            "MCP servers unavailable on attempt %d/%d: %s. Retrying in %.1fs",
            attempt,
            _MCP_TOOL_LOAD_MAX_ATTEMPTS,
            ", ".join(discovery_failures),
            delay_seconds,
        )
        time.sleep(delay_seconds)
        delay_seconds *= 2

    return []


def _wire_agent_graph(available_dependencies: Dict[str, Any], agent_graph: Any) -> None:
    available_dependencies["agent_graph"] = agent_graph
    if "agent_runtime" not in available_dependencies:
        try:
            from agent_sdk.layer4_frameworks.graph.runtime import LangGraphRuntime

            available_dependencies["agent_runtime"] = LangGraphRuntime(agent_graph)
        except ImportError:
            logger.warning(
                "LangGraphRuntime not available; resume functionality may be limited"
            )


def scan_and_load_features(
    features_path: str | None = None, base_module: str | None = None
) -> Dict[str, Any]:
    registry: Dict[str, Any] = {}
    if features_path is None:
        sdk_root = os.path.dirname(os.path.abspath(__file__))
        features_path = os.path.join(sdk_root, "layer2_application", "features")
    if base_module is None:
        base_module = "agent_sdk.layer2_application.features"
    for _, module_name, is_pkg in pkgutil.iter_modules([features_path]):
        if is_pkg:
            try:
                full_module_name = f"{base_module}.{module_name}"
                module = importlib.import_module(full_module_name)
                expected_class_name = f"{_snake_to_pascal(module_name)}UseCase"
                if hasattr(module, expected_class_name):
                    registry[module_name] = getattr(module, expected_class_name)
            except Exception as e:
                logger.warning("Error loading feature %s: %s", module_name, e)
    return registry


def build_app_container(
    features_path: str | None = None,
    base_module: str | None = None,
    extra_dependencies: dict[str, Any] | None = None,
    agent_graph: Any | None = None,
    agent_graph_factory: Callable[[dict[str, Any]], Any] | None = None,
    local_tools: list[Any] | None = None,
    dependency_overrides: dict[str, Any] | None = None,
    dependency_factories: dict[str, Callable[[dict[str, Any]], Any]] | None = None,
    container_hooks: list[Callable[[dict[str, Any]], None]] | None = None,
    inbound_message_adapter: Callable[[Any], Any] | None = None,
    custom_message_handlers: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    resolved_extra_dependencies = dict(extra_dependencies or {})
    resolved_dependency_overrides = dict(dependency_overrides or {})
    resolved_dependency_factories = dict(dependency_factories or {})
    resolved_container_hooks = list(container_hooks or [])
    resolved_custom_message_handlers: dict[str, Any] = {}

    if inbound_message_adapter is None:
        maybe_adapter = resolved_extra_dependencies.pop("inbound_message_adapter", None)
        inbound_message_adapter = maybe_adapter if callable(maybe_adapter) else None
    else:
        resolved_extra_dependencies.pop("inbound_message_adapter", None)
    if agent_graph is None:
        agent_graph = resolved_extra_dependencies.pop("agent_graph", None)
    else:
        resolved_extra_dependencies.pop("agent_graph", None)

    if agent_graph_factory is None:
        agent_graph_factory = resolved_extra_dependencies.pop(
            "agent_graph_factory", None
        )
    else:
        resolved_extra_dependencies.pop("agent_graph_factory", None)
    provided_dependencies = dict(resolved_extra_dependencies)
    if resolved_dependency_overrides:
        provided_dependencies.update(resolved_dependency_overrides)

    runtime_settings = provided_dependencies.get("settings", settings)
    correlation_threads = provided_dependencies.get("correlation_threads", {})
    legacy_handlers = provided_dependencies.get("message_reaction_handlers")
    if isinstance(legacy_handlers, dict):
        resolved_custom_message_handlers.update(legacy_handlers)
    resolved_custom_message_handlers.update(dict(custom_message_handlers or {}))

    if agent_graph is not None and agent_graph_factory is not None:
        raise ValueError("Provide either agent_graph or agent_graph_factory, not both.")
    available_dependencies: Dict[str, Any] = {
        "logger": StandardLogger(),
        "monitor": PrometheusMonitor(),
        "agent_registry": HttpAgentRegistry(),
        "settings": runtime_settings,
        "default_tenant_id": getattr(
            runtime_settings, "DEFAULT_TENANT_ID", settings.DEFAULT_TENANT_ID
        ),
        "correlation_threads": correlation_threads,
    }

    if resolved_extra_dependencies:
        available_dependencies.update(resolved_extra_dependencies)

    if resolved_dependency_overrides:
        available_dependencies.update(resolved_dependency_overrides)

    registry_url = _read_string_setting(runtime_settings, "REGISTRY_URL")
    if registry_url and "tool_registry" not in available_dependencies:
        available_dependencies["tool_registry"] = HttpToolRegistry()

    if "shared_state_repository" not in available_dependencies:
        hana_host = _read_string_setting(runtime_settings, "HANA_HOST")
        if hana_host:
            if "hana_connection_manager" not in available_dependencies:
                from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
                    HanaConnectionManager,
                )

                available_dependencies["hana_connection_manager"] = (
                    HanaConnectionManager(runtime_settings)
                )

            from agent_sdk.layer4_frameworks.persistence.hana.hana_shared_state_repository import (
                HanaSharedStateRepository,
            )

            available_dependencies["shared_state_repository"] = (
                HanaSharedStateRepository(
                    db=available_dependencies["hana_connection_manager"]
                )
            )
            available_dependencies["shared_state_repository"].setup()

    if "agent_shared_state_repository" not in available_dependencies:
        hana_host = _read_string_setting(runtime_settings, "HANA_HOST")
        if hana_host:
            if "hana_connection_manager" not in available_dependencies:
                from agent_sdk.layer4_frameworks.persistence.hana.hana_connection_manager import (
                    HanaConnectionManager,
                )

                available_dependencies["hana_connection_manager"] = (
                    HanaConnectionManager(runtime_settings)
                )

            from agent_sdk.layer4_frameworks.persistence.hana.base_repository import (
                BaseHanaRepository,
            )
            from agent_sdk.layer4_frameworks.persistence.hana.hana_agent_shared_state_repository import (
                HanaAgentSharedStateRepository,
            )

            available_dependencies["agent_shared_state_repository"] = (
                HanaAgentSharedStateRepository(
                    repo=BaseHanaRepository(
                        available_dependencies["hana_connection_manager"],
                        '"AIW_AGENT_STATES"',
                    )
                )
            )
            available_dependencies["agent_shared_state_repository"].setup()

    if "inbox_repository" not in available_dependencies:
        hana_host = _read_string_setting(runtime_settings, "HANA_HOST")
        if hana_host and "hana_connection_manager" in available_dependencies:
            from agent_sdk.layer4_frameworks.persistence.hana.hana_inbox_repository import (
                HanaInboxRepository,
            )

            inbox_repo = HanaInboxRepository(
                db=available_dependencies["hana_connection_manager"]
            )
            inbox_repo.setup()
            available_dependencies["inbox_repository"] = inbox_repo

    if "outbox_repository" not in available_dependencies:
        hana_host = _read_string_setting(runtime_settings, "HANA_HOST")
        if hana_host and "hana_connection_manager" in available_dependencies:
            from agent_sdk.layer4_frameworks.persistence.hana.hana_outbox_repository import (
                HanaOutboxRepository,
            )

            outbox_repo = HanaOutboxRepository(
                db=available_dependencies["hana_connection_manager"]
            )
            outbox_repo.setup()
            available_dependencies["outbox_repository"] = outbox_repo

    if "correlation_thread_store" not in available_dependencies:
        from agent_sdk.layer2_application.services.correlation_thread_store import (
            CorrelationThreadStore,
        )

        available_dependencies["correlation_thread_store"] = CorrelationThreadStore(
            shared_state_repository=available_dependencies.get(
                "shared_state_repository"
            ),
            fallback_threads=available_dependencies["correlation_threads"],
            ttl_seconds=getattr(
                runtime_settings, "AGENT_CALL_CORRELATION_TTL_SECONDS", 3600
            ),
            max_entries=getattr(
                runtime_settings, "AGENT_CALL_CORRELATION_MAX_ENTRIES", 10000
            ),
        )
    llm_service = cast(
        ILLMService | None,
        provided_dependencies.get("llm_service")
        or provided_dependencies.get("chat_completion_service")
        or provided_dependencies.get("openai_service"),
    )
    if llm_service is None and _should_create_llm_service(runtime_settings):
        from agent_sdk.layer4_frameworks.ai.llm_factory import make_llm_service

        llm_service = cast(
            ILLMService,
            make_llm_service(
                settings=runtime_settings,
                logger=available_dependencies.get("logger"),
                monitor=available_dependencies.get("monitor"),
                agent_type=getattr(runtime_settings, "AGENT_TYPE", ""),
                agent_id=getattr(runtime_settings, "AGENT_ID", ""),
            ),
        )

    if llm_service is not None:
        available_dependencies.setdefault("llm_service", llm_service)
        available_dependencies.setdefault("chat_completion_service", llm_service)
        # Compatibility alias for older examples and apps.
        available_dependencies.setdefault("openai_service", llm_service)

    if "llm" not in available_dependencies and llm_service is not None:
        available_dependencies["llm"] = llm_service.get_chat_client()

    local_worker_tools = list(local_tools) if local_tools else []
    worker_tools = list(local_worker_tools)
    available_dependencies["local_worker_tools"] = local_worker_tools
    available_dependencies["worker_tools"] = worker_tools
    available_dependencies["registered_tool_ids"] = {}

    mcp_tools: list[Any] = []

    if getattr(runtime_settings, "MCP_SERVERS", []):
        mcp_client = MCPClientService(
            server_configs=getattr(runtime_settings, "MCP_SERVERS", []),
            logger=available_dependencies["logger"],
        )
        available_dependencies["mcp_client"] = mcp_client
        try:
            mcp_tools = _load_mcp_tools_with_retry(
                mcp_client,
                getattr(runtime_settings, "MCP_TOOL_FILTER", []),
            )
            worker_tools.extend(mcp_tools)
            logger.info("Added %d MCP tools to worker_tools", len(mcp_tools))
        except RuntimeError:
            raise
        except Exception:
            logger.warning("Failed to load MCP tools", exc_info=True)

    if worker_tools and "tool_registry" in available_dependencies:
        tool_registrar = ToolRegistrar(available_dependencies["tool_registry"])
        available_dependencies["registered_tool_ids"] = _run_loader_sync(
            lambda: tool_registrar.register_tools(
                worker_tools,
                owner_kind="agent",
                owner_name=getattr(runtime_settings, "AGENT_TYPE", settings.AGENT_TYPE),
                owner_version=getattr(
                    runtime_settings, "AGENT_VERSION", settings.AGENT_VERSION
                ),
            )
        )

    if agent_graph_factory is not None:
        graph_dependencies = dict(available_dependencies)
        if llm_service is not None:
            graph_dependencies["openai_service"] = llm_service
        resolved_graph = agent_graph_factory(graph_dependencies)
        if resolved_graph is not None:
            _wire_agent_graph(available_dependencies, resolved_graph)
    elif agent_graph is not None:
        if mcp_tools:
            raise ValueError(
                "Bootstrap-loaded MCP tools cannot be attached to a precompiled "
                "agent_graph. Pass agent_graph_factory so the graph is built after "
                "worker_tools are loaded, or prefetch MCP tools before compile."
            )
        _wire_agent_graph(available_dependencies, agent_graph)

    # Keep openai_service as a compatibility alias. New code should inject llm_service.

    publisher_provided = "publisher" in provided_dependencies
    consumer_provided = "consumer" in provided_dependencies
    needs_publisher = not publisher_provided
    needs_consumer = (
        not consumer_provided
        and getattr(runtime_settings, "APP_MODE", settings.APP_MODE).upper() != "SERVER"
    )
    if needs_publisher or needs_consumer:
        from agent_sdk.layer4_frameworks.messaging.factory import create_messaging

        sdk_logger = available_dependencies["logger"]
        broker = _build_broker(runtime_settings)
        messaging = create_messaging(runtime_settings, sdk_logger, broker=broker)
        if needs_publisher:
            available_dependencies["publisher"] = messaging.publisher
        if needs_consumer:
            available_dependencies["consumer"] = messaging.consumer

    if (
        "outbox_repository" in available_dependencies
        and "publisher" in available_dependencies
        and not publisher_provided
    ):
        from agent_sdk.layer2_application.services.outbox_publisher import (
            OutboxPublisher,
        )

        available_dependencies["publisher"] = OutboxPublisher(
            outbox_repository=available_dependencies["outbox_repository"],
            inner_publisher=available_dependencies["publisher"],
            logger=available_dependencies.get("logger"),
        )

    emitter_provided = "workflow_event_emitter" in provided_dependencies
    if not emitter_provided:
        notifier_provided = "push_gateway_notifier" in provided_dependencies
        if not notifier_provided:
            from agent_sdk.layer4_frameworks.messaging.factory import (
                build_push_gateway_notifier,
            )

            available_dependencies["push_gateway_notifier"] = (
                build_push_gateway_notifier(runtime_settings)
            )

        from agent_sdk.layer2_application.services.workflow_event_emitter import (
            WorkflowEventEmitter,
        )

        available_dependencies["workflow_event_emitter"] = WorkflowEventEmitter(
            publisher=available_dependencies["publisher"],
            logger=available_dependencies.get("logger"),
            push_gateway_notifier=available_dependencies.get("push_gateway_notifier"),
        )

    if resolved_dependency_factories:
        for (
            dependency_name,
            dependency_factory,
        ) in resolved_dependency_factories.items():
            available_dependencies[dependency_name] = dependency_factory(
                available_dependencies
            )

    if inbound_message_adapter is not None:
        available_dependencies["inbound_message_adapter"] = inbound_message_adapter
        _wrap_consumer_start_with_adapter(
            available_dependencies.get("consumer"),
            inbound_message_adapter,
            available_dependencies.get("logger"),
        )

    if resolved_custom_message_handlers:
        available_dependencies["message_reaction_handlers"] = (
            resolved_custom_message_handlers
        )

    feature_classes = scan_and_load_features(features_path, base_module)
    container: Dict[str, Any] = {}
    for name, UseCaseClass in feature_classes.items():
        sig = inspect.signature(UseCaseClass)
        injectable_kwargs = {
            param: dep
            for param, dep in available_dependencies.items()
            if param in sig.parameters
        }
        container[name] = UseCaseClass(**injectable_kwargs)

    if (
        "publisher" in available_dependencies
        and "agent_registry" in available_dependencies
        and "agent_delegator" not in available_dependencies
    ):
        from agent_sdk.layer2_application.services.async_agent_delegator import (
            AsyncAgentDelegator,
        )

        available_dependencies["agent_delegator"] = AsyncAgentDelegator(
            publisher=available_dependencies["publisher"],
            registry=available_dependencies["agent_registry"],
            correlation_threads=available_dependencies["correlation_threads"],
            correlation_thread_store=available_dependencies["correlation_thread_store"],
            compatibility_request_topic=_resolve_delegation_request_topic(
                runtime_settings
            ),
            default_request_message_type=_read_string_setting(
                runtime_settings,
                "DELEGATION_REQUEST_MESSAGE_TYPE",
                default="agent.request.agent",
            ),
            default_response_message_type=_read_string_setting(
                runtime_settings,
                "DELEGATION_RESPONSE_MESSAGE_TYPE",
                default="agent.response",
            ),
        )

    container["_dependencies"] = available_dependencies
    for dependency_name in ("consumer", "publisher"):
        if (
            dependency_name in available_dependencies
            and dependency_name not in container
        ):
            container[dependency_name] = available_dependencies[dependency_name]
    if available_dependencies.get("agent_graph") is not None:
        sig = inspect.signature(ResumeAgentUseCase)
        injectable_kwargs = {
            param: dep
            for param, dep in available_dependencies.items()
            if param in sig.parameters
        }
        container["resume_agent"] = ResumeAgentUseCase(**injectable_kwargs)

    if "message_reaction_router" not in container:
        from agent_sdk.layer2_application.services.message_reaction.router import (
            MessageReactionRouter,
        )

        custom_handlers = dict(resolved_custom_message_handlers)
        container["message_reaction_router"] = MessageReactionRouter(
            execute_use_case=container.get("execute_agent"),
            resume_use_case=container.get("resume_agent"),
            custom_handlers=custom_handlers,
            handler_dependencies=available_dependencies,
            correlation_threads=available_dependencies["correlation_threads"],
            correlation_thread_store=available_dependencies["correlation_thread_store"],
            logger=available_dependencies.get("logger"),
        )

    elif resolved_custom_message_handlers:
        router = container.get("message_reaction_router")
        register_many = getattr(router, "register_custom_handlers", None)
        if callable(register_many):
            register_many(resolved_custom_message_handlers)
        else:
            raise TypeError(
                "Provided message_reaction_router does not support register_custom_handlers"
            )

    if resolved_custom_message_handlers:
        container["message_reaction_handlers"] = resolved_custom_message_handlers

    for container_hook in resolved_container_hooks:
        container_hook(container)

    return container


def _build_broker(settings: Any) -> Any:
    messaging_mode = _resolve_infra_mode(settings)

    if messaging_mode == "local":
        try:
            from agent_sdk.layer4_frameworks.messaging.kafka_broker_client import (
                KafkaBrokerClient,
            )

            bootstrap_servers = getattr(
                settings, "KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"
            )
            group_id = getattr(settings, "KAFKA_GROUP_ID", "agent-sdk-consumer")
            reject_topic = getattr(settings, "KAFKA_REJECT_TOPIC", "") or None
            return KafkaBrokerClient(
                bootstrap_servers=bootstrap_servers,
                group_id=group_id,
                kafka_reject_topic=reject_topic,
                max_in_flight_messages=getattr(
                    settings, "CONSUMER_MAX_IN_FLIGHT_MESSAGES", 4
                ),
                shutdown_grace_seconds=getattr(
                    settings, "CONSUMER_SHUTDOWN_GRACE_SECONDS", 30
                ),
            )
        except Exception as exc:
            raise RuntimeError(
                "Failed to initialize KafkaBrokerClient for messaging mode 'local'"
            ) from exc

    if messaging_mode == "sap":
        messaging_protocol = _resolve_event_mesh_protocol(settings)
        management_protocol = _resolve_event_mesh_management_protocol(settings)

        try:
            messaging_url = _resolve_event_mesh_protocol_specific_url(
                settings,
                "EVENT_MESH_MESSAGING",
                messaging_protocol,
                generic_names=("EVENT_MESH_MESSAGING_URL", "EVENT_MESH_BROKER_URL"),
            )
            management_url = _resolve_event_mesh_protocol_specific_url(
                settings,
                "EVENT_MESH_MANAGEMENT",
                management_protocol,
                generic_names=(
                    "EVENT_MESH_MANAGEMENT_URL",
                    "EVENT_MESH_BROKER_URL",
                    "EVENT_MESH_MESSAGING_URL",
                ),
            )
            if not messaging_url:
                raise RuntimeError(
                    f"Missing Event Mesh messaging URL for protocol {messaging_protocol!r}"
                )

            common_kwargs = {
                "token_url": getattr(settings, "EVENT_MESH_TOKEN_URL", ""),
                "messaging_url": messaging_url,
                "management_url": management_url,
                "client_id": getattr(settings, "EVENT_MESH_CLIENT_ID", ""),
                "client_secret": getattr(settings, "EVENT_MESH_CLIENT_SECRET", ""),
                "namespace": getattr(settings, "EVENT_MESH_NAMESPACE", "default"),
                "verify_ssl": getattr(settings, "EVENT_MESH_VERIFY_SSL", True),
                "request_timeout_seconds": getattr(
                    settings, "EVENT_MESH_REQUEST_TIMEOUT_SECONDS", 30.0
                ),
            }
            concurrency_kwargs = {
                "max_in_flight_messages": getattr(
                    settings, "CONSUMER_MAX_IN_FLIGHT_MESSAGES", 4
                ),
                "shutdown_grace_seconds": getattr(
                    settings, "CONSUMER_SHUTDOWN_GRACE_SECONDS", 30
                ),
            }

            if messaging_protocol == "httprest":
                from agent_sdk.layer4_frameworks.messaging.event_mesh_broker_client import (
                    EventMeshBrokerClient,
                )

                broker_factory = cast(Any, EventMeshBrokerClient)
                return broker_factory(
                    **common_kwargs,
                    poll_interval_seconds=getattr(
                        settings, "EVENT_MESH_POLL_INTERVAL_SECONDS", 1.0
                    ),
                    **concurrency_kwargs,
                )

            if messaging_protocol in {"amqp10ws", "amqp10"}:
                from agent_sdk.layer4_frameworks.messaging.event_mesh_amqp_broker_client import (
                    EventMeshAMQPBrokerClient,
                )

                broker_factory = cast(Any, EventMeshAMQPBrokerClient)
                return broker_factory(
                    **common_kwargs,
                    messaging_protocol=messaging_protocol,
                    auth_mode=getattr(settings, "EVENT_MESH_AMQP_AUTH_MODE", "oauth2"),
                    topic_address_template=getattr(
                        settings,
                        "EVENT_MESH_AMQP_TOPIC_ADDRESS_TEMPLATE",
                        "topic:{topic_path}",
                    ),
                    queue_address_template=getattr(
                        settings,
                        "EVENT_MESH_AMQP_QUEUE_ADDRESS_TEMPLATE",
                        "queue:{queue_path}",
                    ),
                    prefetch=getattr(settings, "EVENT_MESH_AMQP_PREFETCH", 10),
                    poll_timeout_seconds=getattr(
                        settings, "EVENT_MESH_AMQP_POLL_TIMEOUT_SECONDS", 1.0
                    ),
                    debug=getattr(settings, "EVENT_MESH_AMQP_DEBUG", False),
                    token_retry_attempts=getattr(
                        settings, "EVENT_MESH_AMQP_TOKEN_RETRY_ATTEMPTS", 3
                    ),
                    token_retry_initial_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_AMQP_TOKEN_RETRY_INITIAL_DELAY_SECONDS",
                        0.2,
                    ),
                    token_retry_max_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_AMQP_TOKEN_RETRY_MAX_DELAY_SECONDS",
                        2.0,
                    ),
                    publish_retry_attempts=getattr(
                        settings, "EVENT_MESH_AMQP_PUBLISH_RETRY_ATTEMPTS", 2
                    ),
                    publish_retry_initial_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_AMQP_PUBLISH_RETRY_INITIAL_DELAY_SECONDS",
                        0.2,
                    ),
                    publish_retry_max_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_AMQP_PUBLISH_RETRY_MAX_DELAY_SECONDS",
                        2.0,
                    ),
                    send_client_cache_max_size=getattr(
                        settings,
                        "EVENT_MESH_AMQP_SEND_CLIENT_CACHE_MAX_SIZE",
                        128,
                    ),
                    send_client_idle_ttl_seconds=getattr(
                        settings,
                        "EVENT_MESH_AMQP_SEND_CLIENT_IDLE_TTL_SECONDS",
                        900.0,
                    ),
                    **concurrency_kwargs,
                )

            if messaging_protocol in {"mqtt311ws", "mqtt311"}:
                from agent_sdk.layer4_frameworks.messaging.event_mesh_mqtt_broker_client import (
                    EventMeshMQTTBrokerClient,
                )

                broker_factory = cast(Any, EventMeshMQTTBrokerClient)
                return broker_factory(
                    **common_kwargs,
                    messaging_protocol=messaging_protocol,
                    auth_mode=getattr(settings, "EVENT_MESH_MQTT_AUTH_MODE", "oauth2"),
                    mqtt_client_id=getattr(settings, "EVENT_MESH_MQTT_CLIENT_ID", ""),
                    topic_template=getattr(
                        settings, "EVENT_MESH_MQTT_TOPIC_TEMPLATE", "{topic_path}"
                    ),
                    queue_template=getattr(
                        settings, "EVENT_MESH_MQTT_QUEUE_TEMPLATE", "{queue_path}"
                    ),
                    qos=getattr(settings, "EVENT_MESH_MQTT_QOS", 1),
                    keepalive_seconds=getattr(
                        settings, "EVENT_MESH_MQTT_KEEPALIVE_SECONDS", 60
                    ),
                    clean_session=getattr(
                        settings, "EVENT_MESH_MQTT_CLEAN_SESSION", True
                    ),
                    reconnect_retries=getattr(
                        settings, "EVENT_MESH_MQTT_RECONNECT_RETRIES", 3
                    ),
                    reconnect_max_interval_seconds=getattr(
                        settings, "EVENT_MESH_MQTT_RECONNECT_MAX_INTERVAL_SECONDS", 10
                    ),
                    connect_retry_attempts=getattr(
                        settings, "EVENT_MESH_MQTT_CONNECT_RETRY_ATTEMPTS", 3
                    ),
                    connect_retry_initial_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_MQTT_CONNECT_RETRY_INITIAL_DELAY_SECONDS",
                        0.2,
                    ),
                    connect_retry_max_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_MQTT_CONNECT_RETRY_MAX_DELAY_SECONDS",
                        2.0,
                    ),
                    publish_retry_attempts=getattr(
                        settings, "EVENT_MESH_MQTT_PUBLISH_RETRY_ATTEMPTS", 2
                    ),
                    publish_retry_initial_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_MQTT_PUBLISH_RETRY_INITIAL_DELAY_SECONDS",
                        0.2,
                    ),
                    publish_retry_max_delay_seconds=getattr(
                        settings,
                        "EVENT_MESH_MQTT_PUBLISH_RETRY_MAX_DELAY_SECONDS",
                        2.0,
                    ),
                    last_will_topic=getattr(
                        settings, "EVENT_MESH_MQTT_LAST_WILL_TOPIC", ""
                    ),
                    last_will_message=getattr(
                        settings, "EVENT_MESH_MQTT_LAST_WILL_MESSAGE", ""
                    ),
                    last_will_qos=getattr(settings, "EVENT_MESH_MQTT_LAST_WILL_QOS", 1),
                    last_will_retain=getattr(
                        settings, "EVENT_MESH_MQTT_LAST_WILL_RETAIN", False
                    ),
                )

            raise RuntimeError(
                f"Unsupported EVENT_MESH_PROTOCOL/EVENT_MESH_MESSAGING_PROTOCOL: {messaging_protocol!r}"
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialize Event Mesh broker client for messaging mode 'sap' "
                f"with protocol {messaging_protocol!r}"
            ) from exc

    return None
