"""Smartsheet event → canonical envelope (smartsheet-event-trigger S3).

Turns one raw Event Reporting event into the SA-2059 envelope the EventTriggerConsumer
routes on, and applies the coarse filter (only mapped ``(objectType, action)`` pairs
emit; everything else is dropped here so the org-wide firehose doesn't flood the
consumer). The envelope's ``event_id`` is derived with the SA-2059 ``EventId.derive``
so a re-drained event yields the same run id downstream and dedups.

⚠️TO-VERIFY against a live 200 (all probes so far were 403 — no sysadmin token):
the event field names (``objectType`` / ``action`` / ``objectId`` / ``eventTimestamp``)
and the real enum values/casing. This module + ``DEFAULT_TOPIC_MAP`` are the ONLY
things that change when the real shape is confirmed — everything else (S1/S2/S4) is
already locked. Keys are lower-cased before mapping so casing drift is absorbed.
"""

from __future__ import annotations

from typing import Any, Callable

from app.layer1_domain.value_objects.event_id import EventId

Normalizer = Callable[[dict], dict | None]

# (objectType, action) -> logical event_topic. Both keys lower-cased.
# ``(objectType, "*")`` matches any action for that object. ⚠️TO-VERIFY enum values.
DEFAULT_TOPIC_MAP: dict[tuple[str, str], str] = {
    ("sheet", "create"): "smartsheet.sheet.created",
    ("sheet", "update"): "smartsheet.sheet.updated",
    ("sheet", "delete"): "smartsheet.sheet.deleted",
    ("row", "create"): "smartsheet.row.created",
    ("row", "update"): "smartsheet.row.updated",
    ("row", "delete"): "smartsheet.row.deleted",
}


def make_normalizer(
    *,
    tenant_id: str,
    topic_map: dict[tuple[str, str], str] | None = None,
    source: str = "smartsheet",
    default_topic: str | None = None,
) -> Normalizer:
    """Build a ``normalize(event) -> envelope | None`` for the poller.

    ``topic_map`` selects which ``(objectType, action)`` pairs emit and onto which
    topic (falls back to ``(objectType, "*")`` then ``default_topic``); an unmapped
    pair with no ``default_topic`` returns ``None`` = coarse-filtered out.
    """
    tmap = DEFAULT_TOPIC_MAP if topic_map is None else topic_map

    def normalize(event: dict) -> dict | None:
        object_type = str(event.get("objectType", "")).lower()
        action = str(event.get("action", "")).lower()

        topic = tmap.get((object_type, action)) or tmap.get((object_type, "*")) or default_topic
        if topic is None:
            return None  # unmapped object/action — dropped at the source

        object_id = event.get("objectId", "")
        occurred_at = str(event.get("eventTimestamp", ""))
        # Stable across re-drains: same (object, action, timestamp) → same id → dedup.
        event_id = EventId.derive(
            f"smartsheet:{object_type}:{object_id}",
            f"{action}:{occurred_at}",
        )
        return {
            "event_id": event_id,
            "event_topic": topic,
            "tenant_id": tenant_id,
            "source": source,
            "occurred_at": occurred_at,
            "payload": event,
        }

    return normalize
