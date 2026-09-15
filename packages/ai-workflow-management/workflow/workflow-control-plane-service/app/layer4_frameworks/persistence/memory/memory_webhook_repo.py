"""In-memory webhook repository."""

from __future__ import annotations

from app.layer1_domain.entities.webhook import Webhook
from app.layer2_application.repositories.webhook_repository_interface import (
    IWebhookRepository,
    ListWebhooksFilter,
    ListWebhooksPage,
)


class MemoryWebhookRepository(IWebhookRepository):
    def __init__(self) -> None:
        self._store: dict[str, Webhook] = {}

    def save(self, webhook: Webhook) -> None:
        self._store[webhook.id] = webhook

    def get_by_id(self, id: str) -> Webhook | None:
        return self._store.get(id)

    def list_by_workflow(self, workflow_id: str) -> list[Webhook]:
        return [w for w in self._store.values() if w.workflow_id == workflow_id]

    def list_paged(self, filters: ListWebhooksFilter) -> ListWebhooksPage:
        items = list(self._store.values())
        if filters.workflow_id:
            items = [w for w in items if w.workflow_id == filters.workflow_id]
        if filters.tenant_id:
            items = [w for w in items if w.tenant_id == filters.tenant_id]
        total = len(items)
        items = items[filters.skip : filters.skip + filters.top]
        return ListWebhooksPage(items=items, total=total)

    def delete(self, id: str) -> None:
        self._store.pop(id, None)
