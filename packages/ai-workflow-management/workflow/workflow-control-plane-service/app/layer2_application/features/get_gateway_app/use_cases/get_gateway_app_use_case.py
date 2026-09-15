"""Get one gateway app. Credential is redacted (auth_summary only)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.gateway_app import GatewayApp
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
)


@dataclass
class GetGatewayAppCommand:
    name: str = ""


@dataclass
class GatewayAppProjection:
    """Public, credential-redacted projection (used by get + list)."""
    id: str
    name: str
    kind: str
    base_url: str
    allowed_hosts: list[str]
    auth_type: str
    auth_summary: dict[str, Any]
    is_active: bool
    description: str
    version: int
    created_by: str
    created_at: str
    updated_by: str
    updated_at: str


@dataclass
class GetGatewayAppResult:
    app: GatewayAppProjection | None = None
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def to_app_projection(entity: GatewayApp) -> GatewayAppProjection:
    return GatewayAppProjection(
        id=entity.id,
        name=entity.name,
        kind=entity.kind.value,
        base_url=entity.base_url or "",
        allowed_hosts=entity.allowed_hosts.to_list() if entity.allowed_hosts else [],
        auth_type=entity.auth_type.value,
        auth_summary=entity.credential_summary(),
        is_active=entity.is_active,
        description=entity.description or "",
        version=entity.version,
        created_by=entity.created_by or "",
        created_at=entity.created_at or "",
        updated_by=entity.updated_by or "",
        updated_at=entity.updated_at or "",
    )


class GetGatewayAppUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = gateway_app_repo

    async def execute(self, command: GetGatewayAppCommand) -> GetGatewayAppResult:
        if not command.name.strip():
            return GetGatewayAppResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "name"},
            )
        entity = self.repo.get_by_name(command.name.strip())
        if entity is None or not entity.is_active:
            return GetGatewayAppResult(
                error=ErrorCode.GATEWAY_APP_NOT_FOUND.default_message,
                error_code=ErrorCode.GATEWAY_APP_NOT_FOUND.code,
                error_details={"name": command.name.strip()},
            )
        return GetGatewayAppResult(app=to_app_projection(entity))
