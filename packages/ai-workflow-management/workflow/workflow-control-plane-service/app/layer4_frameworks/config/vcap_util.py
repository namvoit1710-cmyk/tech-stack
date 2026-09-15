"""
VCAP_SERVICES parser for SAP BTP Cloud Foundry deployments.

Parses service credentials from VCAP_SERVICES environment variable
for SAP HANA and SAP Event Mesh services.

Credentials can be provided via:
1. VCAP_SERVICES (recommended for SAP BTP Cloud Foundry deployments)
2. Environment variables (for local development or non-CF deployments)

VCAP_SERVICES takes priority when available.
"""

import json
import os
import logging
from dataclasses import dataclass
from typing import Optional

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


@dataclass
class EventMeshCredentials:
    """SAP Event Mesh credentials extracted from VCAP_SERVICES."""

    token_url: str
    messaging_url: str
    management_url: str
    client_id: str
    client_secret: str
    namespace: str = "default"
    # AMQP 1.0 over WebSocket endpoint (``amqp10ws`` protocol). Used by the
    # push-based consumer to avoid the consumption-endpoint 429s. Empty when
    # the binding doesn't expose an AMQP protocol.
    amqp_url: str = ""


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

    # Try different HANA service names
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

    # Get the first instance's credentials
    credentials = hana_instances[0].get("credentials", {})

    # Extract required fields
    host = credentials.get("host", "")
    port = credentials.get("port", 443)
    user = credentials.get("user", "")
    password = credentials.get("password", "")
    schema = credentials.get("schema", "")

    # Validate required fields
    if not all([host, user, password]):
        logger.warning("HANA credentials incomplete in VCAP_SERVICES")
        return None

    # Extract optional fields
    encrypt = credentials.get("encrypt", True)
    if isinstance(encrypt, str):
        encrypt = encrypt.lower() == "true"

    certificate = credentials.get("certificate", "")

    # HDI user credentials (for DDL migrations)
    hdi_user = credentials.get("hdi_user", "")
    hdi_password = credentials.get("hdi_password", "")

    logger.info(f"Successfully extracted HANA credentials from VCAP_SERVICES (host: {host})")
    if hdi_user:
        logger.info("HDI user credentials found in VCAP_SERVICES (will be used for migrations)")

    return HanaCredentials(
        host=host,
        port=int(port),
        user=user,
        password=password,
        schema=schema,
        encrypt=encrypt,
        certificate=certificate,
        hdi_user=hdi_user,
        hdi_password=hdi_password,
    )


def _select_event_mesh_instance(instances: list, binding_name: Optional[str]) -> Optional[dict]:
    """Pick a specific instance from a list of Event Mesh service bindings.

    When ``binding_name`` is given, match against the instance's ``name`` /
    ``instance_name`` field. Otherwise return the first instance (back-compat
    with the single-binding behaviour).

    Used by ``get_event_mesh_credentials`` and ``get_event_mesh_secondary_credentials``
    so dual-broker deployments can pick distinct instances by binding name.
    """
    if not instances:
        return None
    if not binding_name:
        return instances[0]
    for inst in instances:
        if inst.get("name") == binding_name or inst.get("instance_name") == binding_name:
            return inst
    logger.warning(
        "Event Mesh binding name %r not found in VCAP_SERVICES; "
        "available: %s",
        binding_name,
        [i.get("name") for i in instances],
    )
    return None


def get_event_mesh_credentials(
    binding_name: Optional[str] = None,
) -> Optional[EventMeshCredentials]:
    """
    Extract SAP Event Mesh credentials from VCAP_SERVICES.

    Looks for service bindings under these service names (in order):
    - "enterprise-messaging"
    - "event-mesh"

    Args:
        binding_name: Optional service-instance name to disambiguate when
            multiple Event Mesh bindings are present. None = first instance.
            Used by dual-broker deployments to pick primary vs secondary.

    Returns:
        EventMeshCredentials if found and valid, None otherwise.
    """
    vcap = _get_vcap_services()
    if not vcap:
        logger.debug("VCAP_SERVICES not available for Event Mesh credentials")
        return None

    # Try different Event Mesh service names
    em_service_names = ["enterprise-messaging", "event-mesh"]
    em_instances = []

    for service_name in em_service_names:
        instances = vcap.get(service_name, [])
        if instances:
            em_instances = instances
            logger.info(f"Found Event Mesh service binding under '{service_name}' ({len(instances)} instance(s))")
            break

    if not em_instances:
        logger.debug("No Event Mesh service binding found in VCAP_SERVICES")
        return None

    selected = _select_event_mesh_instance(em_instances, binding_name)
    if selected is None:
        return None
    credentials = selected.get("credentials", {})

    # Extract OAuth2 credentials
    # SAP Event Mesh uses different credential structures depending on the plan
    # Try to find the OAuth2 token endpoint
    token_url = ""
    client_id = ""
    client_secret = ""

    # Check for 'uaa' nested structure (common in enterprise-messaging)
    uaa = credentials.get("uaa", {})
    if uaa:
        token_url = uaa.get("url", "")
        if token_url and not token_url.endswith("/oauth/token"):
            token_url = f"{token_url.rstrip('/')}/oauth/token"
        client_id = uaa.get("clientid", "")
        client_secret = uaa.get("clientsecret", "")
    else:
        # Direct credentials structure
        token_url = credentials.get("tokenendpoint", "") or credentials.get("oa2token_url", "")
        client_id = credentials.get("clientid", "")
        client_secret = credentials.get("clientsecret", "")

    # Extract messaging URLs
    # Look in different possible locations
    messaging_url = ""
    management_url = ""

    # Check for 'messaging' array (common structure)
    # protocol can be a string ("httprest") or a list (["httprest"])
    amqp_url = ""
    messaging_array = credentials.get("messaging", [])
    if messaging_array:
        for endpoint in messaging_array:
            protocol = endpoint.get("protocol", "")
            uri = endpoint.get("uri", "")
            protocols = protocol if isinstance(protocol, list) else [protocol]
            if "httprest" in protocols and uri and not messaging_url:
                messaging_url = uri
            # AMQP 1.0 over WebSocket endpoint for the push consumer.
            if ("amqp10ws" in protocols or "amqp" in protocols) and uri and not amqp_url:
                amqp_url = uri

    # Check for 'management' array
    # Management endpoints may not have a protocol field — just use the uri directly
    management_array = credentials.get("management", [])
    if management_array:
        for endpoint in management_array:
            uri = endpoint.get("uri", "")
            if uri:
                management_url = uri
                break

    # Fallback to direct URL fields
    if not messaging_url:
        messaging_url = credentials.get("messaging_url", "") or credentials.get("uri", "")
    if not management_url:
        management_url = credentials.get("management_url", "") or messaging_url

    # Extract namespace
    namespace = credentials.get("namespace", "default")

    # Validate required fields
    if not all([token_url, messaging_url, client_id, client_secret]):
        logger.warning(
            f"Event Mesh credentials incomplete in VCAP_SERVICES. "
            f"token_url={bool(token_url)}, messaging_url={bool(messaging_url)}, "
            f"client_id={bool(client_id)}, client_secret={bool(client_secret)}"
        )
        return None

    logger.info(f"Successfully extracted Event Mesh credentials from VCAP_SERVICES")
    logger.info(f"Messaging URL: {messaging_url}")
    logger.info(f"Management URL: {management_url}")

    return EventMeshCredentials(
        token_url=token_url,
        messaging_url=messaging_url,
        management_url=management_url,
        client_id=client_id,
        client_secret=client_secret,
        namespace=namespace,
        amqp_url=amqp_url,
    )


def get_event_mesh_secondary_credentials(
    binding_name: Optional[str] = None,
) -> Optional[EventMeshCredentials]:
    """Extract secondary SAP Event Mesh credentials from VCAP_SERVICES.

    Convenience wrapper around ``get_event_mesh_credentials`` for the
    dual-broker setup: when a deployment is bound to two Event Mesh
    service instances, use ``binding_name`` (from
    ``EVENT_MESH_SECONDARY_BINDING_NAME``) to pick the secondary one.

    Returns None when no secondary binding is configured — call sites
    fall back to the primary broker in that case.
    """
    if not binding_name:
        logger.debug(
            "No secondary Event Mesh binding name configured; "
            "skipping secondary credentials lookup"
        )
        return None
    return get_event_mesh_credentials(binding_name=binding_name)
