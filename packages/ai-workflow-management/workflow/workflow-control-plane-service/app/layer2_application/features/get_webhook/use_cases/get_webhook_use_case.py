"""Get a webhook by ID."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.webhook_repository_interface import IWebhookRepository


@dataclass
class GetWebhookCommand:
    webhook_id: str = ""


@dataclass
class GetWebhookResult:
    webhook: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetWebhookUseCase:
    def __init__(self, logger: ILogger, webhook_repo: IWebhookRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.webhook_repo = webhook_repo

    async def execute(self, command: GetWebhookCommand) -> GetWebhookResult:
        self.logger.info("Getting webhook", id=command.webhook_id)

        webhook = self.webhook_repo.get_by_id(command.webhook_id)
        if not webhook:
            return GetWebhookResult(
                error=ErrorCode.WEBHOOK_NOT_FOUND.default_message,
                error_code=ErrorCode.WEBHOOK_NOT_FOUND.code,
                error_details={"webhook_id": command.webhook_id},
            )

        return GetWebhookResult(webhook=self._to_dict(webhook))

    @staticmethod
    def _to_dict(w: Any) -> dict[str, Any]:
        return {
            "id": w.id,
            "workflow_id": w.workflow_id,
            "name": w.name,
            "url": w.url,
            "secret": w.secret,
            "events": w.events,
            "is_active": w.is_active,
            "tenant_id": w.tenant_id,
            "created_at": w.created_at,
            "updated_at": w.updated_at,
        }
