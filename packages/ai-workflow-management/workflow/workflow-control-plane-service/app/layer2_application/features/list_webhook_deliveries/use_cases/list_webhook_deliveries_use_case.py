"""List webhook deliveries (stub — returns empty list for now)."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.webhook_repository_interface import IWebhookRepository


@dataclass
class ListWebhookDeliveriesCommand:
    webhook_id: str = ""
    skip: int = 0
    top: int = 20


@dataclass
class ListWebhookDeliveriesResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0


class ListWebhookDeliveriesUseCase:
    def __init__(self, logger: ILogger, webhook_repo: IWebhookRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.webhook_repo = webhook_repo

    async def execute(self, command: ListWebhookDeliveriesCommand) -> ListWebhookDeliveriesResult:
        self.logger.info("Listing webhook deliveries", webhook_id=command.webhook_id)

        # Validate that the webhook exists
        webhook = self.webhook_repo.get_by_id(command.webhook_id)
        if not webhook:
            return ListWebhookDeliveriesResult()

        # Delivery tracking not yet implemented — return empty list
        return ListWebhookDeliveriesResult(items=[], total=0)
