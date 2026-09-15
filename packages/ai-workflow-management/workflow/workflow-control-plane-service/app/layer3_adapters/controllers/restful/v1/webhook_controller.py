"""Webhook ingress endpoint (SA-2059 B7 — convergent front door).

Any POST to ``POST /webhooks/{path}`` resolves the matching **inbound
registration**, verifies it (shared secret / optional HMAC / IP allowlist /
tenant binding), normalizes the body with the source adapter, and PUBLISHES the
resulting event(s) onto the registration's ``emit_topic`` — then returns a fast
202. It NEVER starts a run directly: the EventTriggerConsumer routes the topic
to every subscribing workflow (1→N) with dedup + filter. Pattern 1 (direct 1:1
path→StartRun) is retired (design decision 2026-07-16).

    POST http://<host>:<port>/webhooks/<path>
"""

import logging
from typing import Any

from fastapi import APIRouter, Body, Request

from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response
from app.layer2_application.services.webhook_ingress.ingress_service import (
    WebhookIngressService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _client_ip(request: Request) -> str:
    """Real client IP for the allowlist check (F9).

    On Cloud Foundry / BTP the socket peer (``request.client.host``) is the
    gorouter, not the caller — the real client is the left-most hop of
    ``X-Forwarded-For``. ponytail: this trusts the edge proxy to set XFF; if an
    untrusted hop could inject it, pin the trusted-proxy hop count from the right
    instead. Falls back to the socket peer for direct/local calls.
    """
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else ""


@router.post("/{webhook_path:path}")
async def handle_webhook(
    webhook_path: str,
    request: Request,
    body: dict[str, Any] = Body(default={}, examples=[{"order_status": "paid", "order_id": "ORD-001"}]),
) -> Any:
    routing_path = f"/{webhook_path}" if not webhook_path.startswith("/") else webhook_path

    service: WebhookIngressService | None = request.app.state.container.get("webhook_ingress_service")
    if service is None:
        return error_response(
            "SERVICE_UNAVAILABLE",
            "Webhook event ingress is disabled (no broker, or "
            "EVENT_TRIGGER_CONSUMER_ENABLED is off)",
            status_code=503,
        )

    try:
        raw_body = await request.body()
    except Exception:  # noqa: BLE001
        raw_body = b""

    result = service.ingest(
        path=routing_path,
        body=body if isinstance(body, dict) else {"payload": body},
        headers=dict(request.headers),
        raw_body=raw_body,
        client_ip=_client_ip(request),
    )

    logger.info(
        "Webhook ingress: path=%s outcome=%s published=%d",
        routing_path, result.outcome, result.published,
    )

    if result.status_code >= 400:
        code = {401: "UNAUTHORIZED", 404: "NOT_FOUND", 502: "BAD_GATEWAY"}.get(
            result.status_code, "BAD_REQUEST",
        )
        return error_response(code, result.detail, status_code=result.status_code)

    return {
        "status": "accepted",
        "outcome": result.outcome,
        "published": result.published,
        "webhook_path": routing_path,
    }
