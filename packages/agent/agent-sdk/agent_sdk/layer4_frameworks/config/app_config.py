import json
import os
from functools import lru_cache
from typing import Annotated, Any, Optional

from pydantic import ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from agent_sdk.layer4_frameworks.config.vcap_util import (
    _parse_hana_port,
    get_event_mesh_credentials,
    get_hana_credentials,
)


@lru_cache(maxsize=1)
def _cached_hana_credentials():
    return get_hana_credentials()


@lru_cache(maxsize=1)
def _cached_event_mesh_credentials(protocol_hint: str = ""):
    return get_event_mesh_credentials(protocol_hint=protocol_hint)


# ══════════════════════════════════════════════════════════════════════════════
# Load HANA credentials from VCAP_SERVICES (priority) or fall back to env vars
# ══════════════════════════════════════════════════════════════════════════════


def _resolve_hana_defaults() -> dict:
    creds = _cached_hana_credentials()
    if creds and _get_from_vcap_enabled():
        import logging as _logging

        _logging.getLogger(__name__).info("Using HANA credentials from VCAP_SERVICES")
        return {
            "host": creds.host,
            "port": creds.port,
            "username": creds.user,
            "password": creds.password,
            "schema": creds.schema,
            "encrypt": creds.encrypt,
            "ssl_cert": creds.certificate,
        }
    return {
        "host": os.getenv("HANA_HOST", ""),
        "port": _parse_hana_port(os.getenv("HANA_PORT", "443"), "HANA_PORT"),
        "username": os.getenv("HANA_USERNAME", ""),
        "password": os.getenv("HANA_PASSWORD", ""),
        "schema": os.getenv("HANA_SCHEMA", ""),
        "encrypt": os.getenv("HANA_ENCRYPT", "true").lower() == "true",
        "ssl_cert": os.getenv("HANA_SSL_CERT", ""),
    }


def _get_from_vcap_enabled() -> bool:
    return os.getenv("GET_FROM_VCAP", "").lower() in ("true", "1", "yes", "on")


def _resolve_event_mesh_defaults(protocol_hint: str = "") -> dict:
    creds = _cached_event_mesh_credentials(protocol_hint)
    if creds and _get_from_vcap_enabled():
        import logging as _logging

        _logging.getLogger(__name__).info(
            "Using Event Mesh credentials from VCAP_SERVICES"
        )
        return {
            "token_url": creds.token_url,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "messaging_url": creds.messaging_url,
            "management_url": creds.management_url,
            "namespace": creds.namespace,
            "broker_url": creds.broker_url,
            "messaging_protocol": creds.messaging_protocol,
            "management_protocol": creds.management_protocol,
        }
    return {}


def _is_default_event_mesh_url(value: str) -> bool:
    return value.strip().rstrip("/") in {
        "",
        "http://localhost:18080",
        "https://localhost:18080",
    }


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


def _normalize_event_mesh_protocol_name(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "")


def _expand_event_mesh_protocol_preferences(
    value: Any,
    *,
    default: str = "",
) -> list[str]:
    raw_values: list[Any]
    if value is None or value == "":
        raw_values = [default] if default else []
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raw_values = [default] if default else []
        else:
            try:
                parsed = json.loads(stripped)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            raw_values = parsed if isinstance(parsed, list) else stripped.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]

    expanded: list[str] = []
    for raw in raw_values:
        normalized = _normalize_event_mesh_protocol_name(raw)
        for candidate in _EVENT_MESH_PROTOCOL_ALIASES.get(normalized, (normalized,)):
            candidate = _normalize_event_mesh_protocol_name(candidate)
            if candidate and candidate not in expanded:
                expanded.append(candidate)
    return expanded


def _first_event_mesh_protocol(value: Any, *, default: str = "httprest") -> str:
    preferences = _expand_event_mesh_protocol_preferences(value, default=default)
    return preferences[0] if preferences else default


def _get_protocol_specific_event_mesh_url(
    settings_obj: Any,
    prefix: str,
    protocol_preferences: list[str],
) -> str:
    for protocol in protocol_preferences:
        suffix = protocol.upper()
        value = getattr(settings_obj, f"{prefix}_{suffix}_URL", "")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _detect_cf_uri() -> str:
    """Extract the first application URI from VCAP_APPLICATION if running on CF."""
    vcap_raw = os.environ.get("VCAP_APPLICATION", "")
    if not vcap_raw:
        return ""
    try:
        vcap = json.loads(vcap_raw)
        uris = vcap.get("application_uris") or vcap.get("uris") or []
        if uris:
            return uris[0]
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _parse_topic_list(value: Any) -> list[str]:
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
    coerced = str(value).strip()
    return [coerced] if coerced else []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="allow")
    APP_NAME: str = "Agent SDK"
    APP_MODE: str = "SERVER"
    SDK_VERSION: str = "1.0.0"
    AGENT_TYPE: str = "generic"
    AGENT_VERSION: str = "0.1.0"
    AGENT_DOMAIN: str = "general"
    DESCRIPTION: str = "Generic agent powered by Agent SDK"
    CAPABILITIES: list = []
    INPUT_SCHEMA: dict = {}
    OUTPUT_SCHEMA: dict = {}
    REQUIRED_PARAMETERS: list = []
    NEGATIVE_EXAMPLES: list = []
    ROUTING_TIMEOUT_SECONDS: float = 300.0
    REQUIRED_CONFIRMATION: bool = True
    AGENT_KIND: str = "SERVICE"
    IS_PUBLISHED: bool = True
    ATTACHED_AGENT_IDS: list[str] = []
    SYSTEM_PROMPT: str = ""
    MAX_CONCURRENCY: int = 1
    AGENT_CALL_CORRELATION_TTL_SECONDS: int = 3600
    AGENT_CALL_CORRELATION_MAX_ENTRIES: int = 10000
    CONSUMER_MAX_IN_FLIGHT_MESSAGES: int = 4
    CONSUMER_SHUTDOWN_GRACE_SECONDS: int = 30
    CONSUMER_OPS_ENABLED: bool = True
    SUPPORTS_STREAMING: bool = False
    SUPPORTS_HUMAN_IN_THE_LOOP: bool = False
    REGISTRY_URL: str = "http://localhost:8003"
    HEARTBEAT_INTERVAL_SECONDS: int = 30
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_REQUEST_TOPIC: str = "agent.request"
    KAFKA_CONSUME_TOPICS: Annotated[list[str], NoDecode] = []
    KAFKA_RESPONSE_TOPIC: str = "agent.responses"
    EXECUTOR_STATUS_TOPIC: str = ""
    KAFKA_GROUP_ID: str = "agent-sdk-consumer"
    KAFKA_REJECT_TOPIC: str = ""
    QUEUE_NAME: str = ""
    QUEUE_REQUEST_TOPIC: str = ""
    QUEUE_REQUEST_MESSAGE_TYPE: str = ""
    QUEUE_RESPONSE_MESSAGE_TYPE: str = ""
    QUEUE_CONSUME_TOPICS: Annotated[list[str], NoDecode] = []
    QUEUE_REPLY_TOPIC: str = ""
    QUEUE_DELIVERY_HINTS: dict[str, Any] = {}
    OUTBOX_FLUSH_INTERVAL_SECONDS: int = 30
    DELEGATION_REQUEST_TOPIC: str = ""
    DELEGATION_COMPATIBILITY_REQUEST_TOPIC: str = ""
    AGENT_REQUEST_COMPATIBILITY_TOPIC: str = ""
    DELEGATION_REQUEST_MESSAGE_TYPE: str = "agent.request.agent"
    DELEGATION_RESPONSE_MESSAGE_TYPE: str = "agent.response"
    SERVER_HOST: str = "0.0.0.0"
    SERVER_PORT: int = 36000
    AGENT_ADVERTISED_HOST: str = "localhost"
    AGENT_ADVERTISED_URL: str = ""
    AGENT_ADVERTISE_URL: str = ""
    AGENT_ADVERTISE_HOST: str = ""
    AGENT_ADVERTISED_SCHEME: str = ""
    AGENT_ADVERTISE_SCHEME: str = ""
    AGENT_ADVERTISED_PORT: str = ""
    AGENT_ADVERTISE_PORT: str = ""

    # Messaging / transport mode: mock (console/no-op), local (Kafka), sap (Event Mesh)
    INFRA_MODE: str = "mock"
    MESSAGING_MODE: str = ""

    # Push-gateway fan-out (additive; does not replace broker delivery)
    # Set to the push-gateway base URL to enable HTTP fan-out alongside broker publishing.
    PUSH_GATEWAY_URL: str = ""
    PUSH_GATEWAY_TRANSPORT: str = "grpc"
    PUSH_GATEWAY_GRPC_TARGET: str = ""
    PUSH_GATEWAY_BUFFER_ENABLED: bool = True
    PUSH_GATEWAY_BUFFER_MAX_SIZE: int = 256
    PUSH_GATEWAY_BUFFER_ENQUEUE_TIMEOUT_MS: int = 50
    PUSH_GATEWAY_BUFFER_DRAIN_TIMEOUT_SECONDS: int = 5

    # SAP Event Mesh credentials (optional; required only when MESSAGING_MODE=sap)
    EVENT_MESH_PROTOCOL: str = "httprest"
    EVENT_MESH_MESSAGING_PROTOCOL: str = ""
    EVENT_MESH_MANAGEMENT_PROTOCOL: str = "httprest"
    EVENT_MESH_NAMESPACE: str = "default"
    EVENT_MESH_TOKEN_URL: str = ""
    EVENT_MESH_CLIENT_ID: str = ""
    EVENT_MESH_CLIENT_SECRET: str = ""
    EVENT_MESH_MESSAGING_URL: str = "http://localhost:18080"
    EVENT_MESH_BROKER_URL: str = ""
    EVENT_MESH_MANAGEMENT_URL: str = "http://localhost:18080"

    # Protocol-specific local Event Mesh endpoints. These let local .env
    # choose endpoints the same way VCAP protocol selection does. Generic
    # EVENT_MESH_MESSAGING_URL / EVENT_MESH_MANAGEMENT_URL still work and
    # take precedence when explicitly set.
    EVENT_MESH_MESSAGING_HTTPREST_URL: str = ""
    EVENT_MESH_MESSAGING_AMQP10WS_URL: str = ""
    EVENT_MESH_MESSAGING_AMQP10_URL: str = ""
    EVENT_MESH_MESSAGING_MQTT311WS_URL: str = ""
    EVENT_MESH_MESSAGING_MQTT311_URL: str = ""
    EVENT_MESH_MANAGEMENT_HTTPREST_URL: str = ""
    EVENT_MESH_MANAGEMENT_AMQP10WS_URL: str = ""
    EVENT_MESH_MANAGEMENT_AMQP10_URL: str = ""
    EVENT_MESH_MANAGEMENT_MQTT311WS_URL: str = ""
    EVENT_MESH_MANAGEMENT_MQTT311_URL: str = ""
    EVENT_MESH_BROKER_HTTPREST_URL: str = ""
    EVENT_MESH_BROKER_AMQP10WS_URL: str = ""
    EVENT_MESH_BROKER_AMQP10_URL: str = ""
    EVENT_MESH_BROKER_MQTT311WS_URL: str = ""
    EVENT_MESH_BROKER_MQTT311_URL: str = ""
    EVENT_MESH_REQUEST_TOPIC: str = "agent.request"
    EVENT_MESH_CONSUME_TOPICS: Annotated[list[str], NoDecode] = []
    EVENT_MESH_REPLY_TOPIC: str = ""
    # SAP Event Mesh AMQP/MQTT production hardening.
    # Defaults preserve existing behavior: AMQP publish still tries original + one retry;
    # MQTT keeps clean sessions unless explicitly disabled for persistent sessions.
    EVENT_MESH_AMQP_TOKEN_RETRY_ATTEMPTS: int = 3
    EVENT_MESH_AMQP_TOKEN_RETRY_INITIAL_DELAY_SECONDS: float = 0.2
    EVENT_MESH_AMQP_TOKEN_RETRY_MAX_DELAY_SECONDS: float = 2.0
    EVENT_MESH_AMQP_PUBLISH_RETRY_ATTEMPTS: int = 2
    EVENT_MESH_AMQP_PUBLISH_RETRY_INITIAL_DELAY_SECONDS: float = 0.2
    EVENT_MESH_AMQP_PUBLISH_RETRY_MAX_DELAY_SECONDS: float = 2.0

    EVENT_MESH_MQTT_QOS: int = 1
    EVENT_MESH_MQTT_KEEPALIVE_SECONDS: int = 60
    EVENT_MESH_MQTT_CLEAN_SESSION: bool = True
    EVENT_MESH_MQTT_RECONNECT_RETRIES: int = 3
    EVENT_MESH_MQTT_RECONNECT_MAX_INTERVAL_SECONDS: int = 10
    EVENT_MESH_MQTT_CONNECT_RETRY_ATTEMPTS: int = 3
    EVENT_MESH_MQTT_CONNECT_RETRY_INITIAL_DELAY_SECONDS: float = 0.2
    EVENT_MESH_MQTT_CONNECT_RETRY_MAX_DELAY_SECONDS: float = 2.0
    EVENT_MESH_MQTT_PUBLISH_RETRY_ATTEMPTS: int = 2
    EVENT_MESH_MQTT_PUBLISH_RETRY_INITIAL_DELAY_SECONDS: float = 0.2
    EVENT_MESH_MQTT_PUBLISH_RETRY_MAX_DELAY_SECONDS: float = 2.0
    EVENT_MESH_MQTT_LAST_WILL_TOPIC: str = ""
    EVENT_MESH_MQTT_LAST_WILL_MESSAGE: str = ""
    EVENT_MESH_MQTT_LAST_WILL_QOS: int = 1
    EVENT_MESH_MQTT_LAST_WILL_RETAIN: bool = False
    EVENT_MESH_AMQP_SEND_CLIENT_CACHE_MAX_SIZE: int = 128
    EVENT_MESH_AMQP_SEND_CLIENT_IDLE_TTL_SECONDS: float = 900.0

    # SAP HANA configuration (from VCAP_SERVICES or environment variables)
    HANA_HOST: str = ""
    HANA_PORT: int = 443
    HANA_USERNAME: str = ""
    HANA_PASSWORD: str = ""
    HANA_SCHEMA: str = ""
    HANA_ENCRYPT: bool = True
    HANA_SSL_CERT: str = ""
    HANA_POOL_SIZE: int = int(os.getenv("HANA_POOL_SIZE", "5"))
    HANA_MAX_OVERFLOW: int = int(os.getenv("HANA_MAX_OVERFLOW", "10"))
    HANA_POOL_RECYCLE: int = int(os.getenv("HANA_POOL_RECYCLE", "3600"))

    # OpenAI credential remains available for provider env compatibility
    OPENAI_API_KEY: str = ""
    # Generic override for the active LangChain provider. When set, this key is
    # passed to the provider resolved by LLM_PROVIDER/LLM_MODEL, so keep it in
    # sync with the active provider. Provider-specific env vars such as
    # ANTHROPIC_API_KEY or GOOGLE_API_KEY can still be used instead.
    LLM_API_KEY: str = ""

    # Generic provider-agnostic LLM configuration
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_PROVIDER: str = ""
    LLM_TEMPERATURE: float = 0.01
    LLM_TIMEOUT: Optional[float] = None
    LLM_MAX_TOKENS: Optional[int] = None
    LLM_MAX_RETRIES: int = 6
    LLM_MODEL_KWARGS: dict[str, Any] = {}

    LLM_USE_LITELLM_PROXY: bool = False
    LITELLM_PROXY_URL: str = ""
    LITELLM_PROXY_API_KEY: str = ""
    LITELLM_PROXY_MODEL: str = "default"
    # Off by default. Only set this in .env for reasoning-capable models.
    # Examples: low, medium, high. Blank / unset means do not send the parameter.
    LLM_REASONING_EFFORT: Optional[str] = None

    # SDK-level LLM usage/cost telemetry. Keep prices in env because provider
    # pricing changes over time. Keys support either "provider:model" or "model".
    LLM_USAGE_LOG_ENABLED: bool = False
    LLM_USAGE_COST_ENABLED: bool = True
    LLM_USAGE_CURRENCY: str = "USD"
    LLM_USAGE_PRICE_TABLE_JSON: str = "{}"

    # MCP client configuration
    MCP_SERVERS: list[
        dict
    ] = []  # e.g. [{"name": "tools", "url": "http://localhost:8080/mcp"}]
    MCP_TOOL_FILTER: list[
        str
    ] = []  # empty = all tools; non-empty = only these tool names

    # CORS configuration
    ALLOW_ORIGINS: list[str] = ["*"]

    # Multi-tenant defaults — agents that do not manage tenant resolution themselves
    DEFAULT_TENANT_ID: str = "default"

    @model_validator(mode="after")
    def _auto_detect_cf_host(self) -> "Settings":
        """Auto-detect the CF application URI when running on Cloud Foundry.

        Applied on every Settings instance (including subclasses), so agents
        that create their own Settings subclass also get the correct endpoint URL.
        """
        if self.AGENT_ADVERTISED_HOST == "localhost":
            cf_uri = _detect_cf_uri()
            if cf_uri:
                self.AGENT_ADVERTISED_HOST = cf_uri
        return self

    @field_validator(
        "EVENT_MESH_PROTOCOL",
        "EVENT_MESH_MESSAGING_PROTOCOL",
        "EVENT_MESH_MANAGEMENT_PROTOCOL",
        mode="before",
    )
    @classmethod
    def _normalize_event_mesh_protocol(cls, value: Any, info: ValidationInfo) -> str:
        # Keep EVENT_MESH_MESSAGING_PROTOCOL optional so EVENT_MESH_PROTOCOL can
        # act as the default messaging protocol, while still normalizing aliases
        # such as "amqp" -> "amqp10ws" and "mqtt" -> "mqtt311ws".
        default = (
            "" if info.field_name == "EVENT_MESH_MESSAGING_PROTOCOL" else "httprest"
        )
        return _first_event_mesh_protocol(value, default=default)

    @field_validator(
        "KAFKA_CONSUME_TOPICS",
        "EVENT_MESH_CONSUME_TOPICS",
        "QUEUE_CONSUME_TOPICS",
        mode="before",
    )
    @classmethod
    def _parse_topic_list(cls, value: Any) -> list[str]:
        return _parse_topic_list(value)

    @field_validator("PUSH_GATEWAY_TRANSPORT", mode="before")
    @classmethod
    def _normalize_push_gateway_transport(cls, value: str | None) -> str:
        if value is None:
            return "grpc"
        normalized = str(value).strip().lower()
        return normalized or "grpc"

    @field_validator(
        "EVENT_MESH_MQTT_QOS",
        "EVENT_MESH_MQTT_LAST_WILL_QOS",
    )
    @classmethod
    def _validate_mqtt_qos(cls, value: int, info: ValidationInfo) -> int:
        if value not in (0, 1, 2):
            raise ValueError(
                f"{info.field_name} must be one of 0, 1, or 2 (got {value!r})"
            )
        return value

    @field_validator(
        "EVENT_MESH_AMQP_TOKEN_RETRY_ATTEMPTS",
        "EVENT_MESH_AMQP_PUBLISH_RETRY_ATTEMPTS",
        "EVENT_MESH_MQTT_RECONNECT_RETRIES",
        "EVENT_MESH_MQTT_CONNECT_RETRY_ATTEMPTS",
        "EVENT_MESH_MQTT_PUBLISH_RETRY_ATTEMPTS",
    )
    @classmethod
    def _validate_retry_attempts(cls, value: int, info: ValidationInfo) -> int:
        if value < 0:
            raise ValueError(f"{info.field_name} must be >= 0 (got {value!r})")
        return value

    @field_validator(
        "EVENT_MESH_AMQP_TOKEN_RETRY_INITIAL_DELAY_SECONDS",
        "EVENT_MESH_AMQP_TOKEN_RETRY_MAX_DELAY_SECONDS",
        "EVENT_MESH_AMQP_PUBLISH_RETRY_INITIAL_DELAY_SECONDS",
        "EVENT_MESH_AMQP_PUBLISH_RETRY_MAX_DELAY_SECONDS",
        "EVENT_MESH_MQTT_RECONNECT_MAX_INTERVAL_SECONDS",
        "EVENT_MESH_MQTT_CONNECT_RETRY_INITIAL_DELAY_SECONDS",
        "EVENT_MESH_MQTT_CONNECT_RETRY_MAX_DELAY_SECONDS",
        "EVENT_MESH_MQTT_PUBLISH_RETRY_INITIAL_DELAY_SECONDS",
        "EVENT_MESH_MQTT_PUBLISH_RETRY_MAX_DELAY_SECONDS",
    )
    @classmethod
    def _validate_retry_delays(cls, value: float, info: ValidationInfo) -> float:
        if value < 0:
            raise ValueError(f"{info.field_name} must be >= 0 (got {value!r})")
        return value

    @model_validator(mode="after")
    def _resolve_local_event_mesh_protocol_urls(self) -> "Settings":
        # Local .env protocol-specific endpoints mirror VCAP protocol selection.
        # Generic URL env vars still win when explicitly set.
        messaging_preferences = _expand_event_mesh_protocol_preferences(
            self.EVENT_MESH_MESSAGING_PROTOCOL or self.EVENT_MESH_PROTOCOL,
            default="httprest",
        )
        management_preferences = _expand_event_mesh_protocol_preferences(
            self.EVENT_MESH_MANAGEMENT_PROTOCOL,
            default="httprest",
        )

        messaging_url = _get_protocol_specific_event_mesh_url(
            self, "EVENT_MESH_MESSAGING", messaging_preferences
        )
        management_url = _get_protocol_specific_event_mesh_url(
            self, "EVENT_MESH_MANAGEMENT", management_preferences
        )
        broker_url = _get_protocol_specific_event_mesh_url(
            self, "EVENT_MESH_BROKER", messaging_preferences
        )

        # Only pin the resolved messaging protocol onto the field when the user
        # explicitly configured a protocol. Otherwise leave it blank so a later
        # VCAP binding that advertises an AMQP/MQTT protocol can still be adopted
        # (see _resolve_event_mesh_from_vcap). _build_broker falls back to
        # EVENT_MESH_PROTOCOL (default "httprest") when this stays empty.
        messaging_protocol_explicit = bool(
            self.model_fields_set
            & {"EVENT_MESH_MESSAGING_PROTOCOL", "EVENT_MESH_PROTOCOL"}
        )
        if (
            messaging_preferences
            and not self.EVENT_MESH_MESSAGING_PROTOCOL
            and messaging_protocol_explicit
        ):
            self.EVENT_MESH_MESSAGING_PROTOCOL = messaging_preferences[0]
        if management_preferences and not self.EVENT_MESH_MANAGEMENT_PROTOCOL:
            self.EVENT_MESH_MANAGEMENT_PROTOCOL = management_preferences[0]

        if messaging_url and _is_default_event_mesh_url(self.EVENT_MESH_MESSAGING_URL):
            self.EVENT_MESH_MESSAGING_URL = messaging_url
        if management_url and _is_default_event_mesh_url(
            self.EVENT_MESH_MANAGEMENT_URL
        ):
            self.EVENT_MESH_MANAGEMENT_URL = management_url
        if broker_url and not self.EVENT_MESH_BROKER_URL:
            self.EVENT_MESH_BROKER_URL = broker_url
        return self

    @model_validator(mode="after")
    def _resolve_hana_from_vcap(self) -> "Settings":
        defaults = _resolve_hana_defaults()
        if defaults.get("host"):
            if not self.HANA_HOST:
                self.HANA_HOST = defaults["host"]
            if self.HANA_PORT == 443:
                self.HANA_PORT = defaults["port"]
            if not self.HANA_USERNAME:
                self.HANA_USERNAME = defaults["username"]
            if not self.HANA_PASSWORD:
                self.HANA_PASSWORD = defaults["password"]
            if not self.HANA_SCHEMA:
                self.HANA_SCHEMA = defaults["schema"]
            if self.HANA_ENCRYPT is True:
                self.HANA_ENCRYPT = defaults["encrypt"]
            if not self.HANA_SSL_CERT:
                self.HANA_SSL_CERT = defaults["ssl_cert"]
        return self

    @model_validator(mode="after")
    def _resolve_event_mesh_from_vcap(self) -> "Settings":
        defaults = _resolve_event_mesh_defaults(
            self.EVENT_MESH_MESSAGING_PROTOCOL or self.EVENT_MESH_PROTOCOL
        )
        if defaults.get("messaging_url"):
            if not self.MESSAGING_MODE:
                self.MESSAGING_MODE = "sap"
            if self.EVENT_MESH_NAMESPACE == "default" and defaults.get("namespace"):
                self.EVENT_MESH_NAMESPACE = defaults["namespace"]
            if not self.EVENT_MESH_TOKEN_URL:
                self.EVENT_MESH_TOKEN_URL = defaults["token_url"]
            if not self.EVENT_MESH_CLIENT_ID:
                self.EVENT_MESH_CLIENT_ID = defaults["client_id"]
            if not self.EVENT_MESH_CLIENT_SECRET:
                self.EVENT_MESH_CLIENT_SECRET = defaults["client_secret"]
            if _is_default_event_mesh_url(self.EVENT_MESH_MESSAGING_URL):
                self.EVENT_MESH_MESSAGING_URL = defaults["messaging_url"]
            if _is_default_event_mesh_url(self.EVENT_MESH_MANAGEMENT_URL):
                self.EVENT_MESH_MANAGEMENT_URL = defaults["management_url"]
            if not self.EVENT_MESH_BROKER_URL:
                self.EVENT_MESH_BROKER_URL = defaults.get("broker_url", "")
            if not self.EVENT_MESH_MESSAGING_PROTOCOL and defaults.get(
                "messaging_protocol"
            ):
                self.EVENT_MESH_MESSAGING_PROTOCOL = defaults["messaging_protocol"]
            # Only adopt the VCAP-advertised management protocol when the user did
            # not explicitly configure one, so an explicit setting always wins.
            management_protocol_explicit = (
                "EVENT_MESH_MANAGEMENT_PROTOCOL" in self.model_fields_set
            )
            if defaults.get("management_protocol") and not management_protocol_explicit:
                self.EVENT_MESH_MANAGEMENT_PROTOCOL = defaults["management_protocol"]
        return self


settings = Settings()
