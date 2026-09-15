"""Update a function (CAS via expected_version).

Signature changes (method / path_template / input_params) are applied atomically
through ``GatewayFunction.set_signature`` because the path↔param invariant
couples them; other fields use their own domain mutators.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
    CASMismatch,
    IGatewayFunctionRepo,
)

_UNSET: Any = object()


@dataclass
class UpdateGatewayFunctionCommand:
    app_name: str = ""
    name: str = ""                       # current function name (lookup key)
    expected_version: int = 0
    updated_by: str = ""
    new_name: str | None = None
    new_description: str | None = None
    new_method: str | None = None
    new_path_template: str | None = None
    # Use _UNSET sentinel: None could be a valid "clear to empty list".
    new_input_params: Any = _UNSET
    new_output_fields: Any = _UNSET
    new_timeout_ms: int | None = None
    new_icon: str | None = None
    new_color: str | None = None
    new_tags: list[str] | None = None


@dataclass
class UpdateGatewayFunctionResult:
    id: str = ""
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> UpdateGatewayFunctionResult:
    return UpdateGatewayFunctionResult(
        error=message,
        error_code=ErrorCode.VALIDATION_ERROR.code,
        error_details=details or {},
    )


class UpdateGatewayFunctionUseCase:
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
        self, command: UpdateGatewayFunctionCommand
    ) -> UpdateGatewayFunctionResult:
        if not command.expected_version:
            return UpdateGatewayFunctionResult(
                error=ErrorCode.PRECONDITION_REQUIRED.default_message,
                error_code=ErrorCode.PRECONDITION_REQUIRED.code,
            )
        if not command.updated_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="updated_by")

        app = self.app_repo.get_by_name(command.app_name.strip())
        if app is None or not app.is_active:
            return UpdateGatewayFunctionResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"app": command.app_name.strip()},
            )
        fn = self.repo.get_by_app_and_name(app.id, command.name.strip())
        if fn is None or not fn.is_active:
            return UpdateGatewayFunctionResult(
                error=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code,
                error_details={"app": app.name, "name": command.name.strip()},
            )

        by = command.updated_by
        try:
            if command.new_name is not None:
                fn.rename(command.new_name, updated_by=by)
            if command.new_description is not None:
                fn.change_description(command.new_description, updated_by=by)

            # Signature (method / path_template / input_params) — applied atomically.
            sig_method = (
                HttpMethod.coerce(command.new_method)
                if command.new_method is not None
                else None
            )
            sig_path = (
                PathTemplate(command.new_path_template)
                if command.new_path_template is not None
                else None
            )
            sig_params = (
                [FunctionParam.from_dict(p) for p in (command.new_input_params or [])]
                if command.new_input_params is not _UNSET
                else None
            )
            if sig_method is not None or sig_path is not None or sig_params is not None:
                # P4: validate the effective target path against the parent app
                # kind/allowlist before mutating (also catches a host orphaned by
                # a prior app-allowlist change).
                effective_path = sig_path if sig_path is not None else fn.path_template
                if effective_path is not None:
                    validate_function_target(
                        app_kind=app.kind,
                        allowed_hosts=app.allowed_hosts,
                        path_template=effective_path,
                    )
                fn.set_signature(
                    updated_by=by,
                    method=sig_method,
                    path_template=sig_path,
                    input_params=sig_params,
                )

            if command.new_output_fields is not _UNSET:
                fn.set_output_fields(
                    [OutputField.from_dict(o) for o in (command.new_output_fields or [])],
                    updated_by=by,
                )
            if command.new_timeout_ms is not None:
                fn.change_timeout(command.new_timeout_ms, updated_by=by)
            if (
                command.new_icon is not None
                or command.new_color is not None
                or command.new_tags is not None
            ):
                fn.set_presentation(
                    updated_by=by,
                    icon=command.new_icon,
                    color=command.new_color,
                    tags=command.new_tags,
                )
        except ValueError as exc:
            # HttpMethod.coerce raises ValueError on unknown method.
            return _invalid(str(exc), field="method")
        except ValidationError as exc:
            return _invalid(str(exc))

        try:
            self.repo.save(fn, expected_version=command.expected_version)
        except CASMismatch as exc:
            return UpdateGatewayFunctionResult(
                error=ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT.default_message,
                error_code=ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT.code,
                error_details={"detail": str(exc)},
            )
        self.logger.info(
            "Gateway function updated", app=app.name, name=fn.name, id=fn.id,
            version=fn.version, updated_by=fn.updated_by,
        )
        return UpdateGatewayFunctionResult(id=fn.id, version=fn.version)
