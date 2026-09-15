"""VCAP_SERVICES parser for SAP BTP Cloud Foundry deployments.

Extracts SAP Event Mesh credentials from the ``VCAP_SERVICES`` environment
variable so the worker-executor can authenticate against the bound
event-mesh service without requiring per-deployment env-var wiring.

Mirrors the control-plane's ``vcap_util.py`` Event Mesh path. HANA is
intentionally omitted — the worker-executor does not connect to HANA.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class EventMeshCredentials:
    token_url: str
    messaging_url: str
    management_url: str
    client_id: str
    client_secret: str
    namespace: str = "default"
    # AMQP 1.0 over WebSocket endpoint (``amqp10ws``) for the push consumer.
    amqp_url: str = ""


def _get_vcap_services() -> dict:
    vcap_str = os.getenv("VCAP_SERVICES", "")
    if not vcap_str:
        return {}
    try:
        return json.loads(vcap_str)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse VCAP_SERVICES: {e}")
        return {}


def get_event_mesh_credentials() -> Optional[EventMeshCredentials]:
    """Extract SAP Event Mesh credentials from VCAP_SERVICES."""
    vcap = _get_vcap_services()
    if not vcap:
        return None

    em_instances = []
    for service_name in ("enterprise-messaging", "event-mesh"):
        instances = vcap.get(service_name, [])
        if instances:
            em_instances = instances
            logger.info(f"Found Event Mesh service binding under '{service_name}'")
            break

    if not em_instances:
        return None

    credentials = em_instances[0].get("credentials", {})

    token_url = ""
    client_id = ""
    client_secret = ""

    uaa = credentials.get("uaa", {})
    if uaa:
        token_url = uaa.get("url", "")
        if token_url and not token_url.endswith("/oauth/token"):
            token_url = f"{token_url.rstrip('/')}/oauth/token"
        client_id = uaa.get("clientid", "")
        client_secret = uaa.get("clientsecret", "")
    else:
        token_url = credentials.get("tokenendpoint", "") or credentials.get("oa2token_url", "")
        client_id = credentials.get("clientid", "")
        client_secret = credentials.get("clientsecret", "")

    messaging_url = ""
    management_url = ""
    amqp_url = ""

    for endpoint in credentials.get("messaging", []):
        protocol = endpoint.get("protocol", "")
        uri = endpoint.get("uri", "")
        protocols = protocol if isinstance(protocol, list) else [protocol]
        if "httprest" in protocols and uri and not messaging_url:
            messaging_url = uri
        if ("amqp10ws" in protocols or "amqp" in protocols) and uri and not amqp_url:
            amqp_url = uri

    for endpoint in credentials.get("management", []):
        uri = endpoint.get("uri", "")
        if uri:
            management_url = uri
            break

    if not messaging_url:
        messaging_url = credentials.get("messaging_url", "") or credentials.get("uri", "")
    if not management_url:
        management_url = credentials.get("management_url", "") or messaging_url

    namespace = credentials.get("namespace", "default")

    if not all([token_url, messaging_url, client_id, client_secret]):
        logger.warning(
            "Event Mesh credentials incomplete in VCAP_SERVICES. "
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
