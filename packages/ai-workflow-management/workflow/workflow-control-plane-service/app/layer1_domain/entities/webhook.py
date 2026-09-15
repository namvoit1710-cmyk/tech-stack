"""Webhook entity for outbound event delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from app.layer1_domain.entities.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class Webhook(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    workflow_id: str = ""
    name: str = ""
    url: str = ""
    secret: str = ""
    events: list[str] = field(default_factory=list)  # e.g., ["run.completed", "node.failed"]
    is_active: bool = True
    tenant_id: str = "system"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str | None = None
