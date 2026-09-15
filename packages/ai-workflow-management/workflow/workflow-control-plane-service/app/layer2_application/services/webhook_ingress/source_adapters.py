"""Source adapters — payload → normalized event envelope(s) (SA-2059 B8).

Each adapter turns a provider's raw webhook body into zero-or-more canonical
event envelopes ready to publish onto ``workflow.triggers``. An adapter may:
  * derive a stable ``event_id`` (so redelivery dedupes at the run id), and
  * signal a self-trigger loop (return ``None``) so the CP's own writes don't
    re-trigger workflows (Jira B8).

Envelope shape (topic-addressed, NO workflow_id — Pattern 1 retired):
    {event_id, event_topic, tenant_id, source, occurred_at, correlation_id, payload}
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.layer1_domain.entities.webhook_registration import WebhookRegistration
from app.layer1_domain.value_objects.event_id import EventId


@dataclass
class NormalizedEvent:
    event_id: str
    event_topic: str
    payload: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _generic_adapter(
    body: dict[str, Any], headers: dict[str, str], reg: WebhookRegistration,
) -> list[NormalizedEvent] | None:
    """Passthrough: emit one event onto the registration's topic.

    ``event_id`` prefers a caller-supplied id header/body field for dedupe,
    else derives from a hash of the (path, body) so identical redeliveries
    collapse. The consumer's derived run id is the real dedup key regardless.
    """
    event_id = (
        headers.get("x-event-id")
        or (body.get("event_id") if isinstance(body, dict) else None)
        or EventId.derive(f"webhook:{reg.path}", _stable_key(body))
    )
    return [NormalizedEvent(event_id=str(event_id), event_topic=reg.emit_topic, payload=body)]


def _jira_adapter(
    body: dict[str, Any], headers: dict[str, str], reg: WebhookRegistration,
) -> list[NormalizedEvent] | None:
    """Jira Automation 'Send web request' payload → envelope.

    Self-trigger loop guard: if the event's actor matches the registration's
    ``integration_actor`` (the CP's own Jira identity), DROP — otherwise the
    CP's writes back to Jira would re-trigger the same workflows in a storm.

    F10: Jira Automation bodies are fully custom, so the standard webhook shapes
    (``user``/``actor``/``creator``) are commonly absent — the guard then never
    fires. The Automation "Send web request" template MUST therefore include the
    initiator at a documented top-level field::

        {"actor_account_id": "{{initiator.accountId}}", ...}

    We read that field first (reliable for Automation) and fall back to the
    nested webhook shapes for real Jira webhooks.
    """
    issue = body.get("issue") or {}
    fields = issue.get("fields") or {}
    issue_key = issue.get("key") or body.get("issue_key") or ""
    webhook_event = body.get("webhookEvent") or body.get("issue_event_type_name") or "jira.event"

    # Loop guard — prefer the documented smart-value field, then nested shapes.
    if reg.integration_actor:
        actor = body.get("user") or body.get("actor") or fields.get("creator") or {}
        if not isinstance(actor, dict):
            actor = {}
        actor_id = str(
            body.get("actor_account_id")
            or actor.get("accountId")
            or actor.get("emailAddress")
            or actor.get("name")
            or ""
        )
        if actor_id and actor_id == reg.integration_actor:
            return None  # self-triggered — drop

    # Stable event id: (issue key, event, last-updated) — redelivery dedupes.
    updated = fields.get("updated") or issue.get("updated") or ""
    event_id = EventId.derive(f"jira:{issue_key}", f"{webhook_event}:{updated}")
    return [NormalizedEvent(event_id=event_id, event_topic=reg.emit_topic, payload=body)]


def _stable_key(body: Any) -> str:
    import hashlib
    import json

    try:
        serialized = json.dumps(body, sort_keys=True, default=str)
    except (TypeError, ValueError):
        serialized = str(body)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


_ADAPTERS: dict[str, Callable[..., "list[NormalizedEvent] | None"]] = {
    "generic": _generic_adapter,
    "jira": _jira_adapter,
    # github / stripe reuse the generic adapter until their own normalizers land.
    "github": _generic_adapter,
    "stripe": _generic_adapter,
}


def normalize(
    source: str, body: dict[str, Any], headers: dict[str, str], reg: WebhookRegistration,
) -> list[NormalizedEvent] | None:
    """Dispatch to the source's adapter; unknown source → generic passthrough."""
    adapter = _ADAPTERS.get((source or "generic").lower(), _generic_adapter)
    return adapter(body, headers, reg)


def build_envelope(event: NormalizedEvent, reg: WebhookRegistration, correlation_id: str = "") -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "event_topic": event.event_topic,
        "tenant_id": reg.tenant_id,
        "source": reg.source,
        "occurred_at": _now_iso(),
        "correlation_id": correlation_id,
        "payload": event.payload,
    }
