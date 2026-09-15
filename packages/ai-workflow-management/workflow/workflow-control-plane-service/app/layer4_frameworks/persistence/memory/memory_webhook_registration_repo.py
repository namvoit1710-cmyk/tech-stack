"""In-memory inbound webhook registration repository (SA-2059 B7)."""

from __future__ import annotations

from collections import deque

from app.layer1_domain.entities.webhook_registration import (
    WebhookDelivery,
    WebhookRegistration,
)
from app.layer2_application.repositories.webhook_registration_repository_interface import (
    IWebhookRegistrationRepository,
    ListRegistrationsFilter,
    ListRegistrationsPage,
)

_MAX_DELIVERIES_PER_REG = 100


class MemoryWebhookRegistrationRepository(IWebhookRegistrationRepository):
    def __init__(self) -> None:
        self._store: dict[str, WebhookRegistration] = {}
        # registration_id -> bounded newest-last ring of deliveries
        self._deliveries: dict[str, deque[WebhookDelivery]] = {}

    def save(self, registration: WebhookRegistration) -> None:
        self._store[registration.id] = registration

    def get_by_id(self, id: str) -> WebhookRegistration | None:
        return self._store.get(id)

    def get_by_path(self, path: str, tenant_id: str | None = None) -> WebhookRegistration | None:
        norm = path if path.startswith("/") else f"/{path}"
        for reg in self._store.values():
            reg_path = reg.path if reg.path.startswith("/") else f"/{reg.path}"
            if reg_path != norm:
                continue
            if tenant_id is not None and reg.tenant_id != tenant_id:
                continue
            return reg
        return None

    def list_paged(self, filters: ListRegistrationsFilter) -> ListRegistrationsPage:
        items = list(self._store.values())
        if filters.tenant_id:
            items = [r for r in items if r.tenant_id == filters.tenant_id]
        if filters.source:
            items = [r for r in items if r.source == filters.source]
        total = len(items)
        items = items[filters.skip : filters.skip + filters.top]
        return ListRegistrationsPage(items=items, total=total)

    def delete(self, id: str) -> None:
        self._store.pop(id, None)
        self._deliveries.pop(id, None)

    def record_delivery(self, delivery: WebhookDelivery) -> None:
        ring = self._deliveries.setdefault(
            delivery.registration_id, deque(maxlen=_MAX_DELIVERIES_PER_REG)
        )
        ring.append(delivery)

    def list_deliveries(self, registration_id: str, limit: int = 50) -> list[WebhookDelivery]:
        ring = self._deliveries.get(registration_id)
        if not ring:
            return []
        return list(reversed(ring))[:limit]

    def get_delivery(self, registration_id: str, delivery_id: str) -> WebhookDelivery | None:
        ring = self._deliveries.get(registration_id)
        if not ring:
            return None
        return next((d for d in ring if d.id == delivery_id), None)
