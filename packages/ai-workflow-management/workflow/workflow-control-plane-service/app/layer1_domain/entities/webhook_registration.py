"""Inbound webhook registration (SA-2059 B7).

Distinct from ``Webhook`` (that entity is *outbound* delivery — the CP POSTs to
an external ``url`` on run events). A ``WebhookRegistration`` is the *ingress*
side: an external source (Jira / GitHub / Stripe) POSTs to ``POST /webhooks/{path}``,
the CP verifies it, normalizes the payload with the ``source`` adapter, and
publishes an event onto ``emit_topic`` — never a direct StartRun (Pattern 1 is
retired). Cardinality is per-SOURCE, not per-workflow: one registration's URL
fans out to N workflows via the topic (each workflow subscribes with its own
Event trigger).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_entity import BaseDomainEntity


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(kw_only=True)
class WebhookRegistration(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    # Ingress path segment — the URL is POST /webhooks/{path}. Unique per tenant.
    path: str = ""
    # Source adapter key: "jira" | "github" | "stripe" | "generic".
    source: str = "generic"
    # Shared secret the provider echoes in a header (X-Webhook-Secret). Required.
    secret: str = ""
    # Optional HMAC secret — when set, verify an HMAC-SHA256 signature header.
    hmac_secret: str = ""
    # The topic this registration publishes normalized events onto.
    emit_topic: str = ""
    # Optional IP allowlist (CIDR or exact). Empty = allow any.
    ip_allowlist: list[str] = field(default_factory=list)
    # For Jira self-trigger loop guard: events whose actor matches this account
    # id / email are dropped (the CP's own writes must not re-trigger).
    integration_actor: str = ""
    tenant_id: str = "system"
    enabled: bool = True
    created_at: str = field(default_factory=_now_iso)
    updated_at: str | None = None


@dataclass(kw_only=True)
class WebhookDelivery(BaseDomainEntity):
    """A recent ingress attempt — the FE Deliveries log (bounded, newest-first)."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    registration_id: str = ""
    received_at: str = field(default_factory=_now_iso)
    # verified | rejected | filtered | published | dropped_loop | error
    outcome: str = ""
    status_code: int = 0
    event_topic: str = ""
    event_id: str = ""
    detail: str = ""
    # Raw inbound request body (bounded), so the FE can show what actually
    # arrived — the #1 thing you need when a signature/topic won't line up.
    payload: str = ""
