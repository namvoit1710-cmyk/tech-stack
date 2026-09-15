"""List gateway apps (redacted projections)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.features.get_gateway_app.use_cases.get_gateway_app_use_case import (
    GatewayAppProjection,
    to_app_projection,
)
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.gateway_app_repo_interface import (
    IGatewayAppRepo,
    ListGatewayAppsFilter,
)


@dataclass
class ListGatewayAppsCommand:
    name_contains: str = ""
    include_inactive: bool = False
    skip: int = 0
    top: int = 50


@dataclass
class ListGatewayAppsResult:
    items: list[GatewayAppProjection] = field(default_factory=list)
    total: int = 0


class ListGatewayAppsUseCase:
    def __init__(
        self,
        logger: ILogger,
        gateway_app_repo: IGatewayAppRepo,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.repo = gateway_app_repo

    async def execute(self, command: ListGatewayAppsCommand) -> ListGatewayAppsResult:
        page = self.repo.list_paged(
            ListGatewayAppsFilter(
                include_inactive=command.include_inactive,
                skip=max(0, command.skip),
                top=max(1, min(200, command.top)),
                name_contains=command.name_contains.strip(),
            )
        )
        return ListGatewayAppsResult(
            items=[to_app_projection(e) for e in page.items],
            total=page.total,
        )
