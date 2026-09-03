from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI

from agent_sdk.layer1_domain.entities.agent_registration import AgentRegistration
from agent_sdk.layer1_domain.entities.agent_runtime_config import AgentRuntimeConfig
from agent_sdk.layer1_domain.entities.execution_policy import ExecutionPolicy
from agent_sdk.layer1_domain.entities.queue_metadata import QueueMetadata

logger = logging.getLogger(__name__)

_LOCAL_ADVERTISED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}


def _read_advertised_setting(app_settings: Any, *names: str, default: str = "") -> str:
    for name in names:
        env_value = os.getenv(name)
        if isinstance(env_value, str) and env_value.strip():
            return env_value.strip()
        value = getattr(app_settings, name, None) if app_settings is not None else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, str):
            return str(value).strip()
    return default


def _is_absolute_http_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _host_has_port(host: str) -> bool:
    host = host.strip()
    if not host:
        return False
    if host.startswith("["):
        return "]:" in host
    if host.count(":") == 1:
        return host.rsplit(":", 1)[1].isdigit()
    return False


def _is_local_advertised_host(host_or_url: str) -> bool:
    value = host_or_url.strip()
    if _is_absolute_http_url(value):
        parsed = urlsplit(value)
        value = parsed.hostname or value
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value.lower() in _LOCAL_ADVERTISED_HOSTS


def _registry_url_is_remote(app_settings: Any) -> bool:
    registry_url = _read_advertised_setting(app_settings, "REGISTRY_URL")
    if not registry_url or not _is_absolute_http_url(registry_url):
        return False
    parsed = urlsplit(registry_url)
    host = parsed.hostname or ""
    return host.lower() not in _LOCAL_ADVERTISED_HOSTS


def build_routing_metadata(app_settings: Any) -> dict[str, Any]:
    routing_meta: dict[str, Any] = {}
    if getattr(app_settings, "INPUT_SCHEMA", None):
        routing_meta["input_schema"] = app_settings.INPUT_SCHEMA
    if getattr(app_settings, "OUTPUT_SCHEMA", None):
        routing_meta["output_schema"] = app_settings.OUTPUT_SCHEMA
    if getattr(app_settings, "REQUIRED_PARAMETERS", None):
        routing_meta["required_parameters"] = app_settings.REQUIRED_PARAMETERS
    if getattr(app_settings, "NEGATIVE_EXAMPLES", None):
        routing_meta["negative_examples"] = app_settings.NEGATIVE_EXAMPLES
    if getattr(app_settings, "ROUTING_TIMEOUT_SECONDS", 300.0) != 300.0:
        routing_meta["timeout_seconds"] = app_settings.ROUTING_TIMEOUT_SECONDS
    if not getattr(app_settings, "REQUIRED_CONFIRMATION", True):
        routing_meta["required_confirmation"] = False
    return routing_meta


def build_endpoint_url(app_settings: Any = None) -> str:
    if app_settings is None:
        return ""

    explicit_url = _read_advertised_setting(
        app_settings,
        "AGENT_ADVERTISED_URL",
        "AGENT_ADVERTISE_URL",
    )
    if explicit_url:
        if not _is_absolute_http_url(explicit_url):
            raise ValueError(
                "AGENT_ADVERTISED_URL must be a full http(s) URL, for example "
                "http://host.docker.internal:36000 or https://abc.ngrok-free.app"
            )
        endpoint_url = explicit_url.rstrip("/")
        if _is_local_advertised_host(endpoint_url) and _registry_url_is_remote(
            app_settings
        ):
            logger.warning(
                "AGENT_ADVERTISED_URL points to localhost but REGISTRY_URL is remote; "
                "registry health checks will not be able to reach this agent."
            )
        return endpoint_url

    advertised_host = _read_advertised_setting(
        app_settings,
        "AGENT_ADVERTISED_HOST",
        "AGENT_ADVERTISE_HOST",
        default="localhost",
    ).rstrip("/")

    if _is_absolute_http_url(advertised_host):
        endpoint_url = advertised_host.rstrip("/")
        if _is_local_advertised_host(endpoint_url) and _registry_url_is_remote(
            app_settings
        ):
            logger.warning(
                "AGENT_ADVERTISED_HOST points to localhost but REGISTRY_URL is remote; "
                "registry health checks will not be able to reach this agent."
            )
        return endpoint_url

    scheme = _read_advertised_setting(
        app_settings,
        "AGENT_ADVERTISED_SCHEME",
        "AGENT_ADVERTISE_SCHEME",
    ).lower()
    if scheme and scheme not in {"http", "https"}:
        raise ValueError("AGENT_ADVERTISED_SCHEME must be either 'http' or 'https'")

    if not scheme:
        scheme = (
            "https"
            if os.environ.get("VCAP_APPLICATION")
            and not _is_local_advertised_host(advertised_host)
            else "http"
        )

    port = _read_advertised_setting(
        app_settings,
        "AGENT_ADVERTISED_PORT",
        "AGENT_ADVERTISE_PORT",
    )
    if not port:
        if not (os.environ.get("VCAP_APPLICATION") and scheme == "https"):
            port = str(getattr(app_settings, "SERVER_PORT", 36000))

    if port and not port.isdigit():
        raise ValueError("AGENT_ADVERTISED_PORT must be a numeric port")

    if port and not _host_has_port(advertised_host):
        endpoint_url = f"{scheme}://{advertised_host}:{port}"
    else:
        endpoint_url = f"{scheme}://{advertised_host}"

    if _is_local_advertised_host(endpoint_url) and _registry_url_is_remote(
        app_settings
    ):
        logger.warning(
            "The agent is registering a localhost health endpoint with a remote registry. "
            "Set AGENT_ADVERTISED_URL to a URL the registry can reach."
        )

    return endpoint_url.rstrip("/")


def build_agent_runtime_config(app_settings: Any) -> AgentRuntimeConfig | None:
    runtime_config = AgentRuntimeConfig(
        system_prompt=getattr(app_settings, "SYSTEM_PROMPT", ""),
        max_concurrency=getattr(app_settings, "MAX_CONCURRENCY", 1),
        llm_model=getattr(app_settings, "LLM_MODEL", None),
        llm_provider=getattr(app_settings, "LLM_PROVIDER", None),
        llm_temperature=getattr(app_settings, "LLM_TEMPERATURE", None),
        llm_timeout=getattr(app_settings, "LLM_TIMEOUT", None),
        llm_max_tokens=getattr(app_settings, "LLM_MAX_TOKENS", None),
        llm_max_retries=getattr(app_settings, "LLM_MAX_RETRIES", None),
    )
    if runtime_config == AgentRuntimeConfig():
        return None
    return runtime_config


def build_execution_policy(app_settings: Any) -> ExecutionPolicy | None:
    policy = ExecutionPolicy(
        supports_streaming=getattr(app_settings, "SUPPORTS_STREAMING", False),
        supports_human_in_the_loop=getattr(
            app_settings, "SUPPORTS_HUMAN_IN_THE_LOOP", False
        ),
    )
    if policy == ExecutionPolicy():
        return None
    return policy


def _coerce_string_setting(value: Any) -> str:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return ""


def _coerce_mapping_setting(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_topic_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [part.strip() for part in stripped.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def build_queue_metadata(app_settings: Any) -> QueueMetadata | None:
    queue_name = _coerce_string_setting(getattr(app_settings, "QUEUE_NAME", ""))
    request_topic = _coerce_string_setting(
        getattr(app_settings, "QUEUE_REQUEST_TOPIC", "")
    )
    reply_topic = _coerce_string_setting(getattr(app_settings, "QUEUE_REPLY_TOPIC", ""))
    delivery_hints = _coerce_mapping_setting(
        getattr(app_settings, "QUEUE_DELIVERY_HINTS", {})
    )
    request_message_type = _coerce_string_setting(
        getattr(app_settings, "QUEUE_REQUEST_MESSAGE_TYPE", "")
    )
    response_message_type = _coerce_string_setting(
        getattr(app_settings, "QUEUE_RESPONSE_MESSAGE_TYPE", "")
    )
    request_topics = _coerce_topic_list(
        getattr(app_settings, "QUEUE_CONSUME_TOPICS", [])
    )

    app_mode = _coerce_string_setting(getattr(app_settings, "APP_MODE", "")).upper()
    if app_mode in {"CONSUMER", "HYBRID"}:
        messaging_mode = (
            _coerce_string_setting(getattr(app_settings, "MESSAGING_MODE", ""))
            or _coerce_string_setting(getattr(app_settings, "INFRA_MODE", ""))
        ).lower()
        if messaging_mode == "sap":
            if not request_topics:
                request_topics = _coerce_topic_list(
                    getattr(app_settings, "EVENT_MESH_CONSUME_TOPICS", [])
                )
            if not request_topic:
                request_topic = _coerce_string_setting(
                    getattr(app_settings, "EVENT_MESH_REQUEST_TOPIC", "")
                )
            if not reply_topic:
                reply_topic = (
                    _coerce_string_setting(
                        getattr(app_settings, "EVENT_MESH_REPLY_TOPIC", "")
                    )
                    or request_topic
                )
        elif messaging_mode == "local":
            if not request_topics:
                request_topics = _coerce_topic_list(
                    getattr(app_settings, "KAFKA_CONSUME_TOPICS", [])
                )
            if not request_topic:
                request_topic = _coerce_string_setting(
                    getattr(app_settings, "KAFKA_REQUEST_TOPIC", "")
                )
            if not reply_topic:
                reply_topic = _coerce_string_setting(
                    getattr(app_settings, "KAFKA_RESPONSE_TOPIC", "")
                )

    queue_names: list[str] = []
    effective_request_topics = request_topics or (
        [request_topic] if request_topic else []
    )
    if not queue_name and effective_request_topics:
        messaging_mode = (
            getattr(app_settings, "MESSAGING_MODE", "")
            or getattr(app_settings, "INFRA_MODE", "")
        ).lower()
        if messaging_mode == "sap":
            namespace = getattr(app_settings, "EVENT_MESH_NAMESPACE", "default")
            queue_names = [f"{namespace}/{topic}" for topic in effective_request_topics]
            queue_name = queue_names[0]

    if (
        not queue_name
        and not request_topic
        and not request_topics
        and not reply_topic
        and not request_message_type
        and not response_message_type
        and not delivery_hints
    ):
        return None
    return QueueMetadata(
        queue_name=queue_name,
        request_topic=request_topic,
        reply_topic=reply_topic,
        request_message_type=request_message_type,
        response_message_type=response_message_type,
        delivery_hints=delivery_hints,
        request_topics=request_topics,
        queue_names=queue_names,
    )


def build_agent_registration(
    app_settings: Any,
    registered_tool_ids: dict[str, str] | None = None,
) -> AgentRegistration:
    return _build_agent_registration(app_settings, registered_tool_ids)


def _normalize_registry_kind(app_settings: Any) -> tuple[str, str]:
    sdk_kind = getattr(app_settings, "AGENT_KIND", "SERVICE")
    if isinstance(sdk_kind, str) and sdk_kind.lower() == "business":
        return "business", sdk_kind
    return "technical", sdk_kind


def _build_agent_registration(
    app_settings: Any,
    registered_tool_ids: dict[str, str] | None = None,
) -> AgentRegistration:
    capabilities = []
    for action in app_settings.CAPABILITIES or []:
        capabilities.append({"domain": app_settings.AGENT_DOMAIN, "action": action})

    kind, sdk_kind = _normalize_registry_kind(app_settings)
    metadata = {
        "description": app_settings.DESCRIPTION,
        "agent_type": app_settings.AGENT_TYPE,
        "sdk_version": app_settings.SDK_VERSION,
        "agent_domain": app_settings.AGENT_DOMAIN,
        "endpoint_url": build_endpoint_url(app_settings),
        "sdk_kind": sdk_kind,
    }
    routing = build_routing_metadata(app_settings)
    if routing:
        metadata["routing"] = routing

    agent_runtime_config = build_agent_runtime_config(app_settings)
    if agent_runtime_config is not None:
        metadata["agent_runtime_config"] = asdict(agent_runtime_config)

    execution_policy = build_execution_policy(app_settings)
    if execution_policy is not None:
        metadata["execution_policy"] = asdict(execution_policy)

    queue_metadata = build_queue_metadata(app_settings)
    if queue_metadata is not None:
        metadata["queue_metadata"] = asdict(queue_metadata)

    return AgentRegistration(
        agent_type=app_settings.AGENT_TYPE,
        version=app_settings.AGENT_VERSION,
        sdk_version=app_settings.SDK_VERSION,
        domain=app_settings.AGENT_DOMAIN,
        endpoint_url=build_endpoint_url(app_settings),
        capabilities=capabilities,
        kind=kind,
        is_published=getattr(app_settings, "IS_PUBLISHED", True),
        agent_runtime_config=agent_runtime_config,
        execution_policy=execution_policy,
        attached_agent_ids=list(getattr(app_settings, "ATTACHED_AGENT_IDS", [])),
        tool_ids=list((registered_tool_ids or {}).values()),
        queue_metadata=queue_metadata,
        metadata=metadata,
    )


def build_agent_info_payload(container: dict[str, Any]) -> dict[str, Any]:
    info_uc = container.get("get_agent_info")
    if info_uc is None:
        raise KeyError("Container missing 'get_agent_info'")
    domain_output = info_uc.execute()
    return asdict(domain_output)


def attach_ops_routes(app: FastAPI, container: dict[str, Any]) -> None:
    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready():
        return {"status": "ready"}


@asynccontextmanager
async def managed_agent_lifecycle(
    dependencies: dict[str, Any],
) -> AsyncIterator[None]:
    registry = dependencies.get("agent_registry")
    app_settings = dependencies.get("settings")
    state = {"agent_id": None}
    heartbeat_task = None
    if registry is not None and app_settings is not None:
        registration = _build_agent_registration(
            app_settings,
            registered_tool_ids=dependencies.get("registered_tool_ids"),
        )
        try:
            state["agent_id"] = await registry.register(registration)
            logger.info("Registered with registry as %s", state["agent_id"])
        except Exception as exc:
            logger.warning(
                "Initial registration failed: %s — will retry every %ss in background",
                exc,
                app_settings.HEARTBEAT_INTERVAL_SECONDS,
            )

        async def _heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(app_settings.HEARTBEAT_INTERVAL_SECONDS)
                if state["agent_id"] is None:
                    try:
                        state["agent_id"] = await registry.register(registration)
                        logger.info("Registered with registry as %s", state["agent_id"])
                    except Exception as reg_exc:
                        logger.warning("Registration retry failed: %s", reg_exc)
                    continue
                try:
                    await registry.heartbeat(state["agent_id"], "HEALTHY")
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 404:
                        logger.warning(
                            "Heartbeat 404 for %s — executor lost registration, will re-register next interval",
                            state["agent_id"],
                        )
                        state["agent_id"] = None
                    else:
                        logger.warning(
                            "Heartbeat failed (%s): %s",
                            exc.response.status_code,
                            exc,
                        )
                except Exception as exc:
                    logger.warning("Heartbeat failed: %s", exc)

        heartbeat_task = asyncio.create_task(_heartbeat_loop())
    try:
        yield
    finally:
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        tool_registry = dependencies.get("tool_registry")
        registered_tool_ids = list(
            (dependencies.get("registered_tool_ids") or {}).values()
        )
        if tool_registry is not None and registered_tool_ids:
            try:
                await tool_registry.deactivate_tools(registered_tool_ids)
                logger.info("Deactivated %d registered tools", len(registered_tool_ids))
            except Exception as exc:
                logger.warning("Tool deactivation failed: %s", exc)

        if registry is not None and state["agent_id"] is not None:
            try:
                await registry.deregister(state["agent_id"])
                logger.info("Marked agent %s down in registry", state["agent_id"])
            except Exception as exc:
                logger.warning("Deregistration failed: %s", exc)
        client = dependencies.get("agent_registry")
        if client is not None and hasattr(client, "close"):
            try:
                await client.close()
            except (RuntimeError, Exception) as exc:
                logger.warning("Closing agent_registry failed (non-fatal): %s", exc)
        tool_registry = dependencies.get("tool_registry")
        if tool_registry is not None and hasattr(tool_registry, "close"):
            try:
                await tool_registry.close()
            except (RuntimeError, Exception) as exc:
                logger.warning("Closing tool_registry failed (non-fatal): %s", exc)
        push_gateway_notifier = dependencies.get("push_gateway_notifier")
        if push_gateway_notifier is not None and hasattr(
            push_gateway_notifier, "close"
        ):
            try:
                await push_gateway_notifier.close()
            except (RuntimeError, Exception) as exc:
                logger.warning(
                    "Closing push_gateway_notifier failed (non-fatal): %s",
                    exc,
                )
        mcp_client = dependencies.get("mcp_client")
        if mcp_client is not None and hasattr(mcp_client, "close"):
            try:
                await mcp_client.close()
            except (RuntimeError, Exception) as exc:
                logger.warning("Closing mcp_client failed (non-fatal): %s", exc)
