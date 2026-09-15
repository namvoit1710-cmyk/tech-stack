"""Delete a webhook."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.webhook_repository_interface import IWebhookRepository


@dataclass
class DeleteWebhookCommand:
    webhook_id: str = ""


@dataclass
class DeleteWebhookResult:
    deleted: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DeleteWebhookUseCase:
    def __init__(self, logger: ILogger, webhook_repo: IWebhookRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.webhook_repo = webhook_repo

    async def execute(self, command: DeleteWebhookCommand) -> DeleteWebhookResult:
        self.logger.info("Deleting webhook", id=command.webhook_id)

        existing = self.webhook_repo.get_by_id(command.webhook_id)
        if not existing:
            return DeleteWebhookResult(
                error=ErrorCode.WEBHOOK_NOT_FOUND.default_message,
                error_code=ErrorCode.WEBHOOK_NOT_FOUND.code,
                error_details={"webhook_id": command.webhook_id},
            )

        self.webhook_repo.delete(command.webhook_id)
        return DeleteWebhookResult(deleted=True)
