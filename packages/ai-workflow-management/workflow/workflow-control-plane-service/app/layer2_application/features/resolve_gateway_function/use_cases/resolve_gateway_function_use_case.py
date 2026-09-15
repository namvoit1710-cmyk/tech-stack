"""Worker-facing resolve: a function id → a concrete call-spec.

Returns the static request shape (method/path/params/base_url|allowed_hosts) plus
the **decrypted** auth header for the parent app. This is the only endpoint that
exposes the materialized credential, and only to the gateway-worker. Credential
materialization is delegated to the domain (`materialize_auth_header`); the use
case only decrypts (infra) and maps errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.interfaces.credential_cipher_interface import (
    DecryptionFailed,
    ICredentialCipher,
)
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import materialize_auth_header
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)
from app.layer2_application.repositories.gateway_function_repo_interface import (
    IGatewayFunctionRepo,
)


@dataclass
class ResolveGatewayFunctionCommand:
    """Selects a function either by its id, or by (app name, function name).

    A function's id is generated per-environment, so a workflow that pins one
    cannot be promoted dev → qa → prod unchanged. The name pair is stable across
    environments; `get_by_app_and_name` already guarantees a name is unique
    within its app. Exactly one selector must be supplied.
    """
    function_id: str = ""
    app_name: str = ""
    function_name: str = ""


@dataclass
class ResolveGatewayFunctionResult:
    kind: str = ""
    base_url: str = ""
    allowed_hosts: list[str] = field(default_factory=list)
    method: str = ""
    path_template: str = ""
    params: list[dict[str, Any]] = field(default_factory=list)
    timeout_ms: int = 0
    auth_header_name: str = ""
    auth_header_value: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class ResolveGatewayFunctionUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        gateway_function_repo: IGatewayFunctionRepo,
        credential_cipher: ICredentialCipher,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.app_repo = gateway_app_repo
        self.fn_repo = gateway_function_repo
        self.cipher = credential_cipher

    def _locate(
        self, command: ResolveGatewayFunctionCommand
    ) -> tuple[Any, Any] | ResolveGatewayFunctionResult:
        """Find (function, app) from whichever selector the caller supplied.

        Returns the pair, or the not-found Result to hand straight back. Both
        selectors converge here so the credential tail below stays single-path.
        """
        function_id = command.function_id.strip()
        app_name = command.app_name.strip()
        function_name = command.function_name.strip()

        if function_id:
            fn = self.fn_repo.get_by_id(function_id)
            if fn is None or not fn.is_active:
                return ResolveGatewayFunctionResult(
                    error=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.default_message,
                    error_code=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code,
                    error_details={"function_id": function_id},
                )
            app = self.app_repo.get_by_id(fn.app_id)
            if app is None or not app.is_active:
                return ResolveGatewayFunctionResult(
                    error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                    error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                    error_details={"app_id": fn.app_id},
                )
            return fn, app

        if not (app_name and function_name):
            return ResolveGatewayFunctionResult(
                error="Supply either `function_id`, or both `app_name` and `function_name`.",
                error_code=ErrorCode.VALIDATION_ERROR.code,
            )

        app = self.app_repo.get_by_name(app_name)
        if app is None or not app.is_active:
            return ResolveGatewayFunctionResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"app_name": app_name},
            )
        fn = self.fn_repo.get_by_app_and_name(app.id, function_name)
        if fn is None or not fn.is_active:
            return ResolveGatewayFunctionResult(
                error=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_FUNCTION_NOT_FOUND.code,
                error_details={"app_name": app_name, "function_name": function_name},
            )
        return fn, app

    async def execute(
        self, command: ResolveGatewayFunctionCommand
    ) -> ResolveGatewayFunctionResult:
        located = self._locate(command)
        if isinstance(located, ResolveGatewayFunctionResult):
            return located
        fn, app = located

        auth_header_name = ""
        auth_header_value = ""
        if app.auth_type is not AuthType.NONE:
            if app.auth_config_encrypted is None:
                return ResolveGatewayFunctionResult(
                    error=ErrorCode.SERVICE_NOT_CONFIGURED.default_message,
                    error_code=ErrorCode.SERVICE_NOT_CONFIGURED.code,
                    error_details={"app": app.name},
                )
            try:
                plaintext = self.cipher.decrypt(app.auth_config_encrypted)
            except DecryptionFailed as exc:
                # Lost KEK or tampered row — don't leak which.
                self.logger.error(
                    "Gateway app credential decrypt failed",
                    app=app.name,
                    key_version=app.auth_config_encrypted.key_version,
                    detail=str(exc),
                )
                return ResolveGatewayFunctionResult(
                    error=ErrorCode.INTERNAL_ERROR.default_message,
                    error_code=ErrorCode.INTERNAL_ERROR.code,
                )
            try:
                header = materialize_auth_header(app.auth_type, plaintext)
            except NotImplementedError as exc:
                return ResolveGatewayFunctionResult(
                    error=str(exc),
                    error_code=ErrorCode.SERVICE_NOT_CONFIGURED.code,
                    error_details={"auth_type": app.auth_type.value},
                )
            if header is not None:
                auth_header_name, auth_header_value = header.name, header.value

        return ResolveGatewayFunctionResult(
            kind=app.kind.value,
            base_url=app.base_url or "",
            allowed_hosts=app.allowed_hosts.to_list() if app.allowed_hosts else [],
            method=fn.method.value,
            path_template=fn.path_template.value if fn.path_template else "",
            params=[p.to_dict() for p in fn.input_params],
            timeout_ms=fn.timeout_ms,
            auth_header_name=auth_header_name,
            auth_header_value=auth_header_value,
        )
