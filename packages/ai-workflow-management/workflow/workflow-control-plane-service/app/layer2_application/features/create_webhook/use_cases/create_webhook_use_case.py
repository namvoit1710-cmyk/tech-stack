"""Create a new webhook for a workflow."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.webhook_repository_interface import IWebhookRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer1_domain.entities.webhook import Webhook


@dataclass
class CreateWebhookCommand:
    workflow_id: str = ""
    name: str = ""
    url: str = ""
    secret: str = ""
    events: list[str] = field(default_factory=list)
    tenant_id: str = "system"


@dataclass
class CreateWebhookResult:
    id: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class CreateWebhookUseCase:
    def __init__(self, logger: ILogger, webhook_repo: IWebhookRepository,
                 workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.webhook_repo = webhook_repo
        self.workflow_repo = workflow_repo

    async def execute(self, command: CreateWebhookCommand) -> CreateWebhookResult:
        self.logger.info("Creating webhook", name=command.name, workflow_id=command.workflow_id)

        if not command.workflow_id or not command.workflow_id.strip():
            return CreateWebhookResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "workflow_id"},
            )

        if not command.name or not command.name.strip():
            return CreateWebhookResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "name"},
            )

        if not command.url or not command.url.strip():
            return CreateWebhookResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "url"},
            )

        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return CreateWebhookResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        webhook = Webhook(
            workflow_id=command.workflow_id,
            name=command.name.strip(),
            url=command.url.strip(),
            secret=command.secret,
            events=command.events,
            tenant_id=command.tenant_id,
        )
        self.webhook_repo.save(webhook)

        return CreateWebhookResult(id=webhook.id)
