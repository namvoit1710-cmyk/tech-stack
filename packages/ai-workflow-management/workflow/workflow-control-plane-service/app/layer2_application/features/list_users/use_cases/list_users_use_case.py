"""List the workflow app's users from Keycloak (SA-2570).

Central-identity strategy: users live in Keycloak, not a local mirror. This is
the source for the workflow owner picker. Returns lightweight dicts the FE
renders; ``id`` is the Keycloak user id (== token ``sub``), matching the
``owner`` we persist on a workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class ListUsersResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0


class ListUsersUseCase:
    def __init__(self, logger: ILogger, keycloak_admin_client: Any, **kwargs: Any) -> None:
        self.logger = logger
        self.keycloak_admin_client = keycloak_admin_client

    async def execute(self) -> ListUsersResult:
        self.logger.info("Listing users from Keycloak")
        items = await self.keycloak_admin_client.list_users()
        return ListUsersResult(items=items, total=len(items))
