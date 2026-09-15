"""Get one function on an app (by app name + function name)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
)


@dataclass
class GetGatewayFunctionCommand:
    app_name: str = ""
    name: str = ""


@dataclass
class GatewayFunctionProjection:
    id: str
    app_id: str
    name: str
    description: str
    method: str
    path_template: str
    input_params: list[dict[str, Any]]
    output_fields: list[dict[str, Any]]
    timeout_ms: int
    icon: str
    color: str
    tags: list[str]
    is_active: bool
    version: int
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str


@dataclass
class GetGatewayFunctionResult:
    function: GatewayFunctionProjection | None = None
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def to_function_projection(entity: GatewayFunction) -> GatewayFunctionProjection:
    return GatewayFunctionProjection(
        id=entity.id,
        app_id=entity.app_id,
        name=entity.name,
        description=entity.description or "",
        method=entity.method.value,
        path_template=entity.path_template.value if entity.path_template else "",
        input_params=[p.to_dict() for p in entity.input_params],
        output_fields=[o.to_dict() for o in entity.output_fields],
        timeout_ms=entity.timeout_ms,
        icon=entity.icon or "",
        color=entity.color or "",
        tags=list(entity.tags),
        is_active=entity.is_active,
        version=entity.version,
        created_by=entity.created_by or "",
        created_at=entity.created_at or "",
        updated_by=entity.updated_by or "",
        updated_at=entity.updated_at or "",
    )


class GetGatewayFunctionUseCase:
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
        self, command: GetGatewayFunctionCommand
    ) -> GetGatewayFunctionResult:
        app = self.app_repo.get_by_name(command.app_name.strip())
        if app is None or not app.is_active:
            return GetGatewayFunctionResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"app": command.app_name.strip()},
            )
        fn = self.repo.get_by_app_and_name(app.id, command.name.strip())
        if fn is None or not fn.is_active:
            return GetGatewayFunctionResult(
                error=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code,
                error_details={"app": app.name, "name": command.name.strip()},
            )
        return GetGatewayFunctionResult(function=to_function_projection(fn))
