"""Create a function on a gateway app. Orchestration only.

Parent app is resolved by name; the request signature (method + path_template +
params) is built into value objects and validated by ``GatewayFunction.create``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.gateway import (
    FunctionParam,
    HttpMethod,
    OutputField,
    PathTemplate,
    validate_function_target,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
)


@dataclass
class CreateGatewayFunctionCommand:
    app_name: str = ""
    name: str = ""
    method: str = "GET"
    path_template: str = ""
    input_params: list[dict[str, Any]] = field(default_factory=list)
    output_fields: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    timeout_ms: int = 30_000
    icon: str = ""
    color: str = ""
    tags: list[str] = field(default_factory=list)
    created_by: str = ""


@dataclass
class CreateGatewayFunctionResult:
    id: str = ""
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> CreateGatewayFunctionResult:
    return CreateGatewayFunctionResult(
        error=message,
        error_code=ErrorCode.VALIDATION_ERROR.code,
        error_details=details or {},
    )


class CreateGatewayFunctionUseCase:
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
        self, command: CreateGatewayFunctionCommand
    ) -> CreateGatewayFunctionResult:
        app = self.app_repo.get_by_name(command.app_name.strip())
        if app is None or not app.is_active:
            return CreateGatewayFunctionResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"app": command.app_name.strip()},
            )

        if command.name.strip():
            existing = self.repo.get_by_app_and_name(app.id, command.name.strip())
            if existing is not None and existing.is_active:
                return CreateGatewayFunctionResult(
                    error=ErrorCode.GATEWAY_FUNCTION_NAME_TAKEN.default_message,
                    error_code=ErrorCode.GATEWAY_FUNCTION_NAME_TAKEN.code,
                    error_details={"app": app.name, "name": command.name.strip()},
                )

        try:
            method = HttpMethod.coerce(command.method)
        except ValueError:
            return _invalid(
                ErrorCode.VALIDATION_ERROR.default_message,
                field="method",
                allowed=[m.value for m in HttpMethod],
            )

        try:
            path_template = PathTemplate(command.path_template)
            # P4: the target must fit the parent app's kind (internal⇒relative path;
            # external⇒absolute http(s) URL whose host is in the app's allowlist).
            validate_function_target(
                app_kind=app.kind,
                allowed_hosts=app.allowed_hosts,
                path_template=path_template,
            )
            input_params = [FunctionParam.from_dict(p) for p in command.input_params]
            output_fields = [OutputField.from_dict(o) for o in command.output_fields]
            fn = GatewayFunction.create(
                app_id=app.id,
                name=command.name,
                method=method,
                path_template=path_template,
                input_params=input_params,
                output_fields=output_fields,
                description=command.description,
                timeout_ms=command.timeout_ms,
                icon=command.icon,
                color=command.color,
                tags=command.tags,
                created_by=command.created_by,
            )
        except ValidationError as exc:
            return _invalid(str(exc))

        self.repo.save(fn)
        self.logger.info(
            "Gateway function created", app=app.name, name=fn.name, id=fn.id,
            method=fn.method.value, created_by=fn.created_by,
        )
        return CreateGatewayFunctionResult(id=fn.id, version=fn.version)
