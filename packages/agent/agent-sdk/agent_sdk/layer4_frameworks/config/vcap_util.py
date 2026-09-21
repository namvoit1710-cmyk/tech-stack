"""
VCAP_SERVICES parser for SAP BTP Cloud Foundry deployments.

Parses SAP HANA service credentials from the VCAP_SERVICES environment variable.

Credentials can be provided via:
1. VCAP_SERVICES (recommended for SAP BTP Cloud Foundry deployments)
2. Environment variables (for local development or non-CF deployments)

VCAP_SERVICES takes priority when available.
"""

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)


@dataclass
class HanaCredentials:
    """SAP HANA Cloud credentials extracted from VCAP_SERVICES."""

    host: str
    port: int
    user: str
    password: str
    schema: str
    encrypt: bool = True
    certificate: str = ""
    hdi_user: str = ""
    hdi_password: str = ""


def _parse_hana_port(raw_value: object, source_name: str) -> int:
    try:
        return int(cast(Any, raw_value))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid HANA port value for {source_name}: {raw_value!r}"
        ) from exc


def _get_vcap_services() -> dict:
    """
    Parse VCAP_SERVICES environment variable.

    Returns:
        Parsed VCAP_SERVICES as dict, or empty dict if not available.
    """
    vcap_str = os.getenv("VCAP_SERVICES", "")
    if not vcap_str:
        return {}

    try:
        return json.loads(vcap_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse VCAP_SERVICES: {e}")
        return {}


def get_hana_credentials() -> Optional[HanaCredentials]:
    """
    Extract SAP HANA credentials from VCAP_SERVICES.

    Looks for service bindings under these service names (in order):
    - "hana"
    - "hana-cloud"
    - "hanatrial"

    Returns:
        HanaCredentials if found and valid, None otherwise.
    """
    vcap = _get_vcap_services()
    if not vcap:
        logger.debug("VCAP_SERVICES not available for HANA credentials")
        return None

    hana_service_names = ["hana", "hana-cloud", "hanatrial"]
    hana_instances = []

    for service_name in hana_service_names:
        instances = vcap.get(service_name, [])
        if instances:
            hana_instances = instances
            logger.info(f"Found HANA service binding under '{service_name}'")
            break

    if not hana_instances:
        logger.debug("No HANA service binding found in VCAP_SERVICES")
        return None

    credentials = hana_instances[0].get("credentials", {})

    host = credentials.get("host", "")
    port = credentials.get("port", 443)
    user = credentials.get("user", "")
    password = credentials.get("password", "")
    schema = credentials.get("schema", "")

    if not all([host, user, password]):
        logger.warning("HANA credentials incomplete in VCAP_SERVICES")
        return None

    encrypt = credentials.get("encrypt", True)
    if isinstance(encrypt, str):
        encrypt = encrypt.lower() == "true"

    certificate = credentials.get("certificate", "")
    hdi_user = credentials.get("hdi_user", "")
    hdi_password = credentials.get("hdi_password", "")

    logger.info(
        "Successfully extracted HANA credentials from VCAP_SERVICES (host: %s)",
        host,
    )
    if hdi_user:
        logger.info(
            "HDI user credentials found in VCAP_SERVICES (will be used for migrations)"
        )

    return HanaCredentials(
        host=host,
        port=_parse_hana_port(port, "VCAP_SERVICES.hana.credentials.port"),
        user=user,
        password=password,
        schema=schema,
        encrypt=encrypt,
        certificate=certificate,
        hdi_user=hdi_user,
        hdi_password=hdi_password,
    )


@dataclass
class EventMeshCredentials:
    token_url: str
    client_id: str
    client_secret: str
    messaging_url: str
    management_url: str
    namespace: str = "default"
    broker_url: str = ""
    messaging_protocol: str = ""
    management_protocol: str = ""


def _as_list(value: object) -> list[object]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, dict):
        return [value]
    return []


def _deep_get(mapping: Mapping[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        current: object = mapping
        for key in path:
            if not isinstance(current, Mapping):
                current = None
                break
            current = current.get(key)
        if isinstance(current, str) and current.strip():
            return current.strip()
    return ""


def _normalize_protocol_name(value: object) -> str:
    return str(value or "").strip().lower().replace("-", "").replace("_", "")


def _coerce_protocol_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        raw_values = parsed if isinstance(parsed, list) else stripped.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_values = list(value)
    else:
        raw_values = [value]

    result: list[str] = []
    for raw in raw_values:
        normalized = _normalize_protocol_name(raw)
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _expand_protocol_preferences(value: object, *, default: str = "") -> list[str]:
    raw_preferences = _coerce_protocol_values(value)
    if not raw_preferences and default:
        raw_preferences = _coerce_protocol_values(default)

    aliases: dict[str, tuple[str, ...]] = {
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

    expanded: list[str] = []
    for protocol in raw_preferences:
        for candidate in aliases.get(protocol, (protocol,)):
            normalized = _normalize_protocol_name(candidate)
            if normalized and normalized not in expanded:
                expanded.append(normalized)
    return expanded


def _protocols_from_entry(item: Mapping[str, Any]) -> list[str]:
    protocols = item.get("protocol") or item.get("protocols") or []
    return _coerce_protocol_values(protocols)


def _first_dict_by_protocol(items: object, protocol_hint: object) -> dict[str, Any]:
    candidates = [
        cast(dict[str, Any], item) for item in _as_list(items) if isinstance(item, dict)
    ]
    preferences = _expand_protocol_preferences(protocol_hint)

    for preferred_protocol in preferences:
        for item in candidates:
            if preferred_protocol in _protocols_from_entry(item):
                return item

    return candidates[0] if candidates else {}


def _event_mesh_messaging_protocol_hint(explicit_hint: object = None) -> str:
    return (
        str(explicit_hint or "").strip()
        or os.getenv("EVENT_MESH_PROTOCOL", "").strip()
        or os.getenv("EVENT_MESH_MESSAGING_PROTOCOL", "").strip()
        or "httprest"
    )


def _event_mesh_management_protocol_hint() -> str:
    return os.getenv("EVENT_MESH_MANAGEMENT_PROTOCOL", "").strip() or "httprest"


def _iter_event_mesh_instances(vcap: Mapping[str, Any]) -> list[dict[str, Any]]:
    preferred_service_names = (
        "enterprise-messaging",
        "enterprise-messaging-hub",
        "event-mesh",
        "eventmesh",
        "message-queuing",
        "messaging",
    )
    instances: list[dict[str, Any]] = []
    for service_name in preferred_service_names:
        for instance in _as_list(vcap.get(service_name)):
            if isinstance(instance, dict):
                instances.append(cast(dict[str, Any], instance))
    if instances:
        return instances

    # Fallback for service plans/labels that differ between landscapes.
    for service_instances in vcap.values():
        for instance in _as_list(service_instances):
            if not isinstance(instance, dict):
                continue
            instance_map = cast(dict[str, Any], instance)
            label = str(
                instance_map.get("label") or instance_map.get("name") or ""
            ).lower()
            credentials = instance_map.get("credentials")
            if not isinstance(credentials, dict):
                continue
            if (
                "messaging" in label
                or "event" in label
                or credentials.get("messaging")
                or credentials.get("management")
            ):
                instances.append(cast(dict[str, Any], instance))
    return instances


def _extract_event_mesh_credentials(
    credentials: Mapping[str, Any],
    *,
    protocol_hint: object = None,
) -> EventMeshCredentials | None:
    messaging = _first_dict_by_protocol(
        credentials.get("messaging"),
        _event_mesh_messaging_protocol_hint(protocol_hint),
    )
    management = _first_dict_by_protocol(
        credentials.get("management"),
        _event_mesh_management_protocol_hint(),
    )
    raw_messaging_oauth = messaging.get("oa2")
    raw_management_oauth = management.get("oa2")
    messaging_oauth: dict[str, Any] = (
        cast(dict[str, Any], raw_messaging_oauth)
        if isinstance(raw_messaging_oauth, dict)
        else {}
    )
    management_oauth: dict[str, Any] = (
        cast(dict[str, Any], raw_management_oauth)
        if isinstance(raw_management_oauth, dict)
        else {}
    )

    # Explicit token endpoints already carry the /oauth/token path and are
    # authoritative. The bare `uaa.url` is only the base XSUAA host, so it is
    # tried LAST (and gets /oauth/token appended below) — never ahead of the
    # nested messaging/management tokenendpoint.
    token_url = _deep_get(
        credentials,
        ("token_url",),
        ("tokenurl",),
        ("tokenendpoint",),
        ("oauth", "token_url"),
        ("oauth", "tokenurl"),
        ("oauth", "tokenendpoint"),
    )
    token_url = token_url or _deep_get(
        messaging_oauth, ("tokenendpoint",), ("tokenurl",), ("token_url",)
    )
    token_url = token_url or _deep_get(
        management_oauth, ("tokenendpoint",), ("tokenurl",), ("token_url",)
    )
    token_url = token_url or _deep_get(credentials, ("uaa", "url"))
    # A bare XSUAA authentication host (any BTP region: sap/br10/us10/eu10/...) is
    # not itself a token endpoint — append /oauth/token. Region-agnostic on purpose.
    normalized_token_url = token_url.rstrip("/") if token_url else ""
    if (
        normalized_token_url
        and not normalized_token_url.endswith("/oauth/token")
        and ".authentication." in normalized_token_url
        and normalized_token_url.endswith(".hana.ondemand.com")
    ):
        token_url = f"{normalized_token_url}/oauth/token"

    client_id = _deep_get(
        credentials,
        ("clientid",),
        ("client_id",),
        ("oauth", "clientid"),
        ("oauth", "client_id"),
        ("uaa", "clientid"),
        ("uaa", "client_id"),
    )
    client_id = client_id or _deep_get(messaging_oauth, ("clientid",), ("client_id",))
    client_id = client_id or _deep_get(management_oauth, ("clientid",), ("client_id",))

    client_secret = _deep_get(
        credentials,
        ("clientsecret",),
        ("client_secret",),
        ("oauth", "clientsecret"),
        ("oauth", "client_secret"),
        ("uaa", "clientsecret"),
        ("uaa", "client_secret"),
    )
    client_secret = client_secret or _deep_get(
        messaging_oauth, ("clientsecret",), ("client_secret",)
    )
    client_secret = client_secret or _deep_get(
        management_oauth, ("clientsecret",), ("client_secret",)
    )

    messaging_url = _deep_get(
        messaging,
        ("uri",),
        ("url",),
        ("broker", "url"),
        ("broker", "uri"),
    ) or _deep_get(
        credentials, ("messaging_url",), ("messagingUrl",), ("uri",), ("url",)
    )
    management_url = _deep_get(management, ("uri",), ("url",)) or _deep_get(
        credentials,
        ("management_url",),
        ("managementUrl",),
    )
    if not management_url:
        management_url = messaging_url

    namespace = (
        _deep_get(credentials, ("namespace",), ("emname",), ("xsappname",)) or "default"
    )
    broker_url = (
        _deep_get(credentials, ("broker_url",), ("brokerUrl",)) or messaging_url
    )

    messaging_protocols = _protocols_from_entry(messaging)
    management_protocols = _protocols_from_entry(management)
    messaging_protocol = messaging_protocols[0] if messaging_protocols else ""
    management_protocol = management_protocols[0] if management_protocols else ""

    if not all([token_url, client_id, client_secret, messaging_url]):
        return None

    return EventMeshCredentials(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        messaging_url=messaging_url,
        management_url=management_url,
        namespace=namespace,
        broker_url=broker_url,
        messaging_protocol=messaging_protocol,
        management_protocol=management_protocol,
    )


def get_event_mesh_credentials(
    *,
    protocol_hint: object = None,
) -> Optional[EventMeshCredentials]:
    vcap = _get_vcap_services()
    if not vcap:
        logger.debug("VCAP_SERVICES not available for Event Mesh credentials")
        return None

    for instance in _iter_event_mesh_instances(vcap):
        credentials = instance.get("credentials", {})
        if not isinstance(credentials, dict):
            continue
        extracted = _extract_event_mesh_credentials(
            credentials, protocol_hint=protocol_hint
        )
        if extracted is not None:
            logger.info(
                "Successfully extracted Event Mesh credentials from VCAP_SERVICES"
            )
            return extracted

    logger.debug("No Event Mesh service binding found in VCAP_SERVICES")
    return None
