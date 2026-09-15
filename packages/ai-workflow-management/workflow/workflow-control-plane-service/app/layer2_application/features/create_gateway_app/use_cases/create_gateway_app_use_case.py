"""Create a gateway app. Orchestration only — all rules live in the domain.

Flow: parse value objects → domain ``validate_auth_config`` → encrypt (infra) →
``GatewayApp.create`` (domain) → repo.save. Domain ``ValidationError`` maps to
VALIDATION_ERROR; the use case never re-implements field validation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.interfaces.credential_cipher_interface import ICredentialCipher
from app.layer1_domain.value_objects.auth_type import AuthType
from app.layer1_domain.value_objects.gateway import (
    AppKind,
    HostAllowlist,
    validate_auth_config,
)
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)


@dataclass
class CreateGatewayAppCommand:
    name: str = ""
    kind: str = "internal"
    base_url: str = ""
    allowed_hosts: list[str] = field(default_factory=list)
    auth_type: str = "none"
    auth_config: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    spec_url: str = ""
    created_by: str = ""


@dataclass
class CreateGatewayAppResult:
    id: str = ""
    version: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _invalid(message: str, **details: Any) -> CreateGatewayAppResult:
    return CreateGatewayAppResult(
        error=message,
        error_code=ErrorCode.VALIDATION_ERROR.code,
        error_details=details or {},
    )


class CreateGatewayAppUseCase:
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

    async def execute(self, command: CreateGatewayAppCommand) -> CreateGatewayAppResult:
        try:
            kind = AppKind(command.kind)
        except ValueError:
            return _invalid(
                ErrorCode.VALIDATION_ERROR.default_message,
                field="kind",
                allowed=[k.value for k in AppKind],
            )
        try:
            auth_type = AuthType(command.auth_type)
        except ValueError:
            return _invalid(
                ErrorCode.VALIDATION_ERROR.default_message,
                field="auth_type",
                allowed=[t.value for t in AuthType],
            )

        try:
            validate_auth_config(auth_type, command.auth_config)
        except ValidationError as exc:
            return _invalid(str(exc), field="auth_config")

        if command.name.strip():
            existing = self.repo.get_by_name(command.name.strip())
            # Soft-deleted rows count. IDX_AIW_GW_APPS_NAME is unique across
            # ALL rows (migration 043 says so outright), so letting an inactive
            # name through here does not create the app — it reaches repo.save,
            # INSERTs a second row with that name, and dies on the constraint
            # as a 500. A 409 that names the cause is the honest answer.
            if existing is not None:
                return CreateGatewayAppResult(
                    error=(
                        ErrorCode.GATEWAY_APP_NAME_TAKEN.default_message
                        if existing.is_active
                        else (
                            f"name '{command.name.strip()}' is reserved by a deleted "
                            "app and is not released on delete — choose another name"
                        )
                    ),
                    error_code=ErrorCode.GATEWAY_APP_NAME_TAKEN.code,
                    error_details={
                        "name": command.name.strip(),
                        "existing_is_active": existing.is_active,
                        "existing_id": existing.id,
                    },
                )

        try:
            allowed = (
                HostAllowlist.from_list(command.allowed_hosts)
                if kind is AppKind.EXTERNAL
                else None
            )
        except ValidationError as exc:
            return _invalid(str(exc), field="allowed_hosts")

        credential = (
            self.cipher.encrypt(command.auth_config)
            if auth_type is not AuthType.NONE
            else None
        )

        try:
            app = GatewayApp.create(
                name=command.name,
                kind=kind,
                base_url=command.base_url,
                allowed_hosts=allowed,
                auth_type=auth_type,
                auth_config_encrypted=credential,
                description=command.description,
                spec_url=command.spec_url,
                created_by=command.created_by,
            )
        except ValidationError as exc:
            return _invalid(str(exc))

        self.repo.save(app)
        self.logger.info(
            "Gateway app created",
            name=app.name,
            id=app.id,
            kind=app.kind.value,
            created_by=app.created_by,
        )
        return CreateGatewayAppResult(id=app.id, version=app.version)
