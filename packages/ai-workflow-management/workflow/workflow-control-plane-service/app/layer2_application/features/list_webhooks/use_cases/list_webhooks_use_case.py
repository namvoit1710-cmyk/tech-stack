"""List webhooks with optional pagination and filtering."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.webhook_repository_interface import (
    IWebhookRepository,
    ListWebhooksFilter,
)


@dataclass
class ListWebhooksCommand:
    workflow_id: str | None = None
    tenant_id: str | None = None
    skip: int = 0
    top: int = 20


@dataclass
class ListWebhooksResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0


class ListWebhooksUseCase:
    def __init__(self, logger: ILogger, webhook_repo: IWebhookRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.webhook_repo = webhook_repo

    async def execute(self, command: ListWebhooksCommand) -> ListWebhooksResult:
        self.logger.info("Listing webhooks", workflow_id=command.workflow_id)

        filters = ListWebhooksFilter(
            workflow_id=command.workflow_id,
            tenant_id=command.tenant_id,
            skip=command.skip,
            top=command.top,
        )
        page = self.webhook_repo.list_paged(filters)
        items = [self._to_dict(w) for w in page.items]
        return ListWebhooksResult(items=items, total=page.total)

    @staticmethod
    def _to_dict(w: Any) -> dict[str, Any]:
        return {
            "id": w.id,
            "workflow_id": w.workflow_id,
            "name": w.name,
            "url": w.url,
            "events": w.events,
            "is_active": w.is_active,
            "tenant_id": w.tenant_id,
            "created_at": w.created_at,
            "updated_at": w.updated_at,
        }
