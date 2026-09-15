"""Inbound webhook registration repository interface (SA-2059 B7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.layer1_domain.entities.webhook_registration import (
    WebhookDelivery,
    WebhookRegistration,
)


@dataclass
class ListRegistrationsFilter:
    tenant_id: str | None = None
    source: str | None = None
    skip: int = 0
    top: int = 50


@dataclass
class ListRegistrationsPage:
    items: list[WebhookRegistration] = field(default_factory=list)
    total: int = 0


class IWebhookRegistrationRepository(Protocol):
    def save(self, registration: WebhookRegistration) -> None: ...
    def get_by_id(self, id: str) -> WebhookRegistration | None: ...
    def get_by_path(self, path: str, tenant_id: str | None = None) -> WebhookRegistration | None: ...
    def list_paged(self, filters: ListRegistrationsFilter) -> ListRegistrationsPage: ...
    def delete(self, id: str) -> None: ...

    # Deliveries log (bounded, newest-first) — the FE Deliveries tab.
    def record_delivery(self, delivery: WebhookDelivery) -> None: ...
    def list_deliveries(self, registration_id: str, limit: int = 50) -> list[WebhookDelivery]: ...
    # Single delivery incl. its raw payload — fetched on row click (list omits payload).
    def get_delivery(self, registration_id: str, delivery_id: str) -> WebhookDelivery | None: ...
