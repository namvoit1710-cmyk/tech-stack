"""Smartsheet Event Reporting stream cursor (smartsheet-event-trigger S1).

The Smartsheet poller drains ``GET /2.0/events``, which paginates via a
``nextStreamPosition`` token. To resume across restarts without losing or
replaying events, that token is persisted here — one cursor per source/tenant
(``id`` = e.g. ``"smartsheet:<tenant_id>"``). An empty ``stream_position`` means
"not bootstrapped yet"; the poller then starts from a ``since`` timestamp on its
first drain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.layer1_domain.entities.base_entity import BaseDomainEntity


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(kw_only=True)
class SmartsheetStreamCursor(BaseDomainEntity):
    # Cursor key — one per source/tenant (e.g. "smartsheet:<tenant_id>").
    id: str = "smartsheet"
    # Smartsheet ``nextStreamPosition`` token; "" = not bootstrapped yet.
    stream_position: str = ""
    updated_at: str = field(default_factory=_now_iso)
