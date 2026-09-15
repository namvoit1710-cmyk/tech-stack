"""Webhook repository interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.layer1_domain.entities.webhook import Webhook


@dataclass
class ListWebhooksFilter:
    workflow_id: str | None = None
    tenant_id: str | None = None
    skip: int = 0
    top: int = 20


@dataclass
class ListWebhooksPage:
    items: list[Webhook] = field(default_factory=list)
    total: int = 0


class IWebhookRepository(Protocol):
    def save(self, webhook: Webhook) -> None: ...
    def get_by_id(self, id: str) -> Webhook | None: ...
    def list_by_workflow(self, workflow_id: str) -> list[Webhook]: ...
    def list_paged(self, filters: ListWebhooksFilter) -> ListWebhooksPage: ...
    def delete(self, id: str) -> None: ...
