"""Soft-delete a function on an app (is_active=False)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
)


@dataclass
class DeleteGatewayFunctionCommand:
    app_name: str = ""
    name: str = ""
    deleted_by: str = ""


@dataclass
class DeleteGatewayFunctionResult:
    deleted: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DeleteGatewayFunctionUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        gateway_function_repo: IGatewayFunctionRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.app_repo = gateway_app_repo
        self.repo = gateway_function_repo

    async def execute(
        self, command: DeleteGatewayFunctionCommand
    ) -> DeleteGatewayFunctionResult:
        app = self.app_repo.get_by_name(command.app_name.strip())
        if app is None or not app.is_active:
            return DeleteGatewayFunctionResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"app": command.app_name.strip()},
            )
        fn = self.repo.get_by_app_and_name(app.id, command.name.strip())
        if fn is None or not fn.is_active:
            return DeleteGatewayFunctionResult(
                error=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code,
                error_details={"app": app.name, "name": command.name.strip()},
            )
        self.repo.soft_delete(fn.id)
        self.logger.info(
            "Gateway function soft-deleted", app=app.name, name=fn.name, id=fn.id,
            deleted_by=command.deleted_by,
        )
        return DeleteGatewayFunctionResult(deleted=True)
