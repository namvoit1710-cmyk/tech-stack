"""Update an existing webhook."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.webhook_repository_interface import IWebhookRepository


@dataclass
class UpdateWebhookCommand:
    webhook_id: str = ""
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


@dataclass
class UpdateWebhookResult:
    updated: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class UpdateWebhookUseCase:
    def __init__(self, logger: ILogger, webhook_repo: IWebhookRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.webhook_repo = webhook_repo

    async def execute(self, command: UpdateWebhookCommand) -> UpdateWebhookResult:
        self.logger.info("Updating webhook", id=command.webhook_id)

        webhook = self.webhook_repo.get_by_id(command.webhook_id)
        if not webhook:
            return UpdateWebhookResult(
                error=ErrorCode.WEBHOOK_NOT_FOUND.default_message,
                error_code=ErrorCode.WEBHOOK_NOT_FOUND.code,
                error_details={"webhook_id": command.webhook_id},
            )

        if command.name is not None:
            if not command.name.strip():
                return UpdateWebhookResult(
                    error=ErrorCode.VALIDATION_ERROR.default_message,
                    error_code=ErrorCode.VALIDATION_ERROR.code,
                    error_details={"field": "name"},
                )
            webhook.name = command.name.strip()
        if command.url is not None:
            if not command.url.strip():
                return UpdateWebhookResult(
                    error=ErrorCode.VALIDATION_ERROR.default_message,
                    error_code=ErrorCode.VALIDATION_ERROR.code,
                    error_details={"field": "url"},
                )
            webhook.url = command.url.strip()
        if command.secret is not None:
            webhook.secret = command.secret
        if command.events is not None:
            webhook.events = command.events
        if command.is_active is not None:
            webhook.is_active = command.is_active

        webhook.updated_at = datetime.now(timezone.utc).isoformat()
        self.webhook_repo.save(webhook)

        return UpdateWebhookResult(updated=True)
