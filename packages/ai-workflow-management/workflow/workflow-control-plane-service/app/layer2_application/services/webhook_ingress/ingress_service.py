"""Webhook ingress service (SA-2059 B7) — verify + normalize + publish + 202.

The convergent front door. Every external source POSTs to ``POST /webhooks/{path}``;
this service resolves the registration, VERIFIES it (shared secret, optional
HMAC, IP allowlist, tenant binding — tenant always from the registration, never
the payload), NORMALIZES via the source adapter, and PUBLISHES the resulting
event(s) onto the registration's ``emit_topic``. It NEVER calls StartRun —
Jira / GitHub / Stripe all inherit the EventTriggerConsumer's dedup, filter and
fan-out through the topic (Pattern 1 retired, design decision 2026-07-16).
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
from dataclasses import dataclass
from typing import Any, Callable

from app.layer1_domain.entities.webhook_registration import WebhookDelivery
from app.layer2_application.repositories.webhook_registration_repository_interface import (
    IWebhookRegistrationRepository,
)
from app.layer2_application.services.webhook_ingress import source_adapters

logger = logging.getLogger(__name__)

_SECRET_HEADER = "x-webhook-secret"
# Jira Cloud native WebHook signs with the "Secret" field and sends
# ``X-Hub-Signature: sha256=<hmac>`` (no ``-256`` suffix). GitHub uses
# ``X-Hub-Signature-256``. We compute SHA-256 either way and try each header.
_HMAC_HEADERS = ("x-hub-signature-256", "x-hub-signature", "x-webhook-signature")


def _ip_allowed(client_ip: str, allowlist: list[str]) -> bool:
    """True if client_ip matches any allowlist entry (exact IP or CIDR)."""
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for entry in allowlist:
        entry = (entry or "").strip()
        if not entry:
            continue
        try:
            if "/" in entry:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            elif ip == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue  # ponytail: skip malformed allowlist entries, don't crash ingress
    return False


@dataclass
class IngressResult:
    status_code: int
    outcome: str  # verified | rejected | published | dropped_loop | error
    detail: str = ""
    published: int = 0


class WebhookIngressService:
    def __init__(
        self,
        registration_repo: IWebhookRegistrationRepository,
        publish: Callable[[str, Any], None],
    ) -> None:
        self._repo = registration_repo
        self._publish = publish

    def ingest(
        self,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str],
        raw_body: bytes = b"",
        client_ip: str = "",
    ) -> IngressResult:
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        reg = self._repo.get_by_path(path)
        if reg is None or not reg.enabled:
            # 404 for unregistered paths — Pattern 1 direct-workflow lookup is gone.
            return IngressResult(404, "rejected", f"No enabled registration for path '{path}'")

        # The exact bytes we verified, stored on the delivery so the FE can show
        # them. ponytail: cap at 64KB — Jira payloads run ~10-30KB and the log is
        # bounded to 100/reg; a runaway body shouldn't bloat the row.
        payload = (raw_body or b"").decode("utf-8", "replace")[:65536]

        verify_err = self._verify(reg, headers, raw_body, client_ip)
        if verify_err is not None:
            self._record(reg, "rejected", 401, detail=verify_err, payload=payload)
            return IngressResult(401, "rejected", verify_err)

        events = source_adapters.normalize(reg.source, body, headers, reg)
        if events is None:
            # Self-trigger loop guard fired (Jira B8) — accept but do nothing.
            self._record(reg, "dropped_loop", 202, detail="self-triggered event dropped", payload=payload)
            return IngressResult(202, "dropped_loop", "self-triggered event dropped")

        correlation_id = headers.get("x-correlation-id", "")
        published = 0
        for event in events:
            if not event.event_topic:
                self._record(reg, "error", 202, detail="registration has no emit_topic", payload=payload)
                continue
            envelope = source_adapters.build_envelope(event, reg, correlation_id)
            try:
                self._publish(event.event_topic, envelope)
            except Exception as exc:  # noqa: BLE001 - publish failure must yield a clear 5xx
                logger.error("Webhook publish failed: path=%s topic=%s err=%s", path, event.event_topic, exc)
                self._record(reg, "error", 502, event_topic=event.event_topic,
                             event_id=event.event_id, detail=str(exc), payload=payload)
                return IngressResult(502, "error", f"publish failed: {exc}", published)
            published += 1
            self._record(reg, "published", 202, event_topic=event.event_topic,
                         event_id=event.event_id, payload=payload)

        return IngressResult(202, "published", f"published {published} event(s)", published)

    # -- verification ------------------------------------------------------

    def _verify(self, reg: Any, headers: dict[str, str], raw_body: bytes, client_ip: str) -> str | None:
        """Return an error string when verification fails, else ``None``."""
        # 0. F7: fail closed. A registration with neither a shared secret nor an
        # HMAC secret cannot authenticate the caller — an IP allowlist alone is a
        # network control, not proof of payload origin. Reject rather than pass
        # everything (create/update also refuse to persist such a registration).
        if not reg.secret and not reg.hmac_secret:
            return "registration has no verification configured (shared secret or HMAC required)"

        # 1. IP allowlist — exact IP or CIDR (F9). client_ip is the trusted
        # X-Forwarded-For hop resolved in the controller.
        if reg.ip_allowlist and client_ip and not _ip_allowed(client_ip, reg.ip_allowlist):
            return f"client ip {client_ip} not in allowlist"

        # 2. Shared secret header (constant-time compare).
        if reg.secret:
            provided = headers.get(_SECRET_HEADER, "")
            if not hmac.compare_digest(provided, reg.secret):
                return "shared secret mismatch"

        # 3. Optional HMAC signature over the raw body.
        if reg.hmac_secret:
            sig = ""
            for h in _HMAC_HEADERS:
                if h in headers:
                    sig = headers[h]
                    break
            expected = "sha256=" + hmac.new(
                reg.hmac_secret.encode("utf-8"), raw_body, hashlib.sha256
            ).hexdigest()
            # accept both "sha256=<hex>" and bare "<hex>"
            if not (hmac.compare_digest(sig, expected)
                    or hmac.compare_digest(sig, expected[len("sha256="):])):
                return "HMAC signature mismatch"

        return None

    def _record(
        self, reg: Any, outcome: str, status_code: int,
        event_topic: str = "", event_id: str = "", detail: str = "", payload: str = "",
    ) -> None:
        try:
            self._repo.record_delivery(WebhookDelivery(
                registration_id=reg.id, outcome=outcome, status_code=status_code,
                event_topic=event_topic, event_id=event_id, detail=detail, payload=payload,
            ))
        except Exception:  # noqa: BLE001 - delivery logging must never break ingress
            logger.warning("Failed to record webhook delivery for registration %s", reg.id)
