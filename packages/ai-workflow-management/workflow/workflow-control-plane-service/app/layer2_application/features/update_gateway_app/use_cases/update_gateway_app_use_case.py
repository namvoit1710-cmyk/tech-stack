"""Update a gateway app (CAS via expected_version).

Every change is applied through a GatewayApp domain method (rename /
change_base_url / set_allowed_hosts / set_auth / ...). The use case only parses
input, encrypts (infra), and translates domain ``ValidationError`` / repo
``CASMismatch`` to error codes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.interfaces.credential_cipher_interface import ICredentialCipher
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import HostAllowlist, validate_auth_config
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    CASMismatch,
    IGatewayAppRepo,
)


@dataclass
class UpdateGatewayAppCommand:
    name: str = ""                       # path arg — current name (lookup key)
    expected_version: int = 0            # from If-Match header
    updated_by: str = ""
    # Partial-update fields. ``None`` (or omitted) = leave alone.
    new_name: str | None = None
    new_description: str | None = None
    new_base_url: str | None = None
    new_allowed_hosts: list[str] | None = None
    new_auth_type: str | None = None
    new_auth_config: dict[str, Any] | None = None
    new_spec_url: str | None = None


@dataclass
class UpdateGatewayAppResult:
    id: str = ""
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> UpdateGatewayAppResult:
    return UpdateGatewayAppResult(
        error=message,
        error_code=ErrorCode.VALIDATION_ERROR.code,
        error_details=details or {},
    )


class UpdateGatewayAppUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        credential_cipher: ICredentialCipher,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = gateway_app_repo
        self.cipher = credential_cipher

    async def execute(self, command: UpdateGatewayAppCommand) -> UpdateGatewayAppResult:
        if not command.name.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="name")
        if not command.expected_version:
            return UpdateGatewayAppResult(
                error=ErrorCode.PRECONDITION_REQUIRED.default_message,
                error_code=ErrorCode.PRECONDITION_REQUIRED.code,
            )
        if not command.updated_by.strip():
            return _invalid(ErrorCode.VALIDATION_ERROR.default_message, field="updated_by")

        app = self.repo.get_by_name(command.name.strip())
        if app is None or not app.is_active:
            return UpdateGatewayAppResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"name": command.name.strip()},
            )

        by = command.updated_by
        try:
            if command.new_name is not None:
                app.rename(command.new_name, updated_by=by)
            if command.new_description is not None:
                app.change_description(command.new_description, updated_by=by)
            if command.new_base_url is not None:
                app.change_base_url(command.new_base_url, updated_by=by)
            if command.new_allowed_hosts is not None:
                app.set_allowed_hosts(
                    HostAllowlist.from_list(command.new_allowed_hosts), updated_by=by
                )
            if command.new_spec_url is not None:
                app.set_spec_url(command.new_spec_url, updated_by=by)
            self._apply_auth(app, command)
        except ValidationError as exc:
            return _invalid(str(exc))

        try:
            self.repo.save(app, expected_version=command.expected_version)
        except CASMismatch as exc:
            return UpdateGatewayAppResult(
                error=ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT.default_message,
                error_code=ErrorCode.OPTIMISTIC_CONCURRENCY_CONFLICT.code,
                error_details={"detail": str(exc)},
            )
        self.logger.info(
            "Gateway app updated", name=app.name, id=app.id, version=app.version,
            updated_by=app.updated_by,
        )
        return UpdateGatewayAppResult(id=app.id, version=app.version)

    def _apply_auth(self, app: Any, command: UpdateGatewayAppCommand) -> None:
        """Translate the (auth_type, auth_config) inputs into a single domain
        ``set_auth``/``clear_auth`` call. Raises ``ValidationError`` on bad input."""
        if command.new_auth_type is None and command.new_auth_config is None:
            return
        target_type = app.auth_type
        if command.new_auth_type is not None:
            try:
                target_type = AuthType(command.new_auth_type)
            except ValueError:
                raise ValidationError(
                    f"invalid auth_type; allowed: {[t.value for t in AuthType]}"
                )

        if command.new_auth_config is not None:
            validate_auth_config(target_type, command.new_auth_config)
            credential = (
                self.cipher.encrypt(command.new_auth_config)
                if target_type is not AuthType.NONE
                else None
            )
            app.set_auth(target_type, credential, updated_by=command.updated_by)
        elif target_type is AuthType.NONE:
            app.clear_auth(updated_by=command.updated_by)
        elif target_type is not app.auth_type:
            # Switching to a credential-bearing type without supplying one.
            raise ValidationError(
                f"changing auth_type to {target_type.value} requires auth_config"
            )
