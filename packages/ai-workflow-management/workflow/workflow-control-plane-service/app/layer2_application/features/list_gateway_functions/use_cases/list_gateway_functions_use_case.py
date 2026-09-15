"""List functions of an app (redacted projections)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.features.get_gateway_function.use_cases.get_gateway_function_use_case import (
    GatewayFunctionProjection,
    to_function_projection,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
    ListGatewayFunctionsFilter,
)


@dataclass
class ListGatewayFunctionsCommand:
    app_name: str = ""
    name_contains: str = ""
    include_inactive: bool = False
    skip: int = 0
    top: int = 50


@dataclass
class ListGatewayFunctionsResult:
    items: list[GatewayFunctionProjection] = field(default_factory=list)
    total: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ListGatewayFunctionsUseCase:
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
        self, command: ListGatewayFunctionsCommand
    ) -> ListGatewayFunctionsResult:
        app = self.app_repo.get_by_name(command.app_name.strip())
        if app is None or not app.is_active:
            return ListGatewayFunctionsResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"app": command.app_name.strip()},
            )
        page = self.repo.list_by_app(
            ListGatewayFunctionsFilter(
                app_id=app.id,
                include_inactive=command.include_inactive,
                skip=max(0, command.skip),
                top=max(1, min(200, command.top)),
                name_contains=command.name_contains.strip(),
            )
        )
        return ListGatewayFunctionsResult(
            items=[to_function_projection(e) for e in page.items],
            total=page.total,
        )
