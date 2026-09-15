from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.layer1_domain.entities.gateway_function import GatewayFunction
# CASMismatch is shared across both gateway repos — defined once on the app repo.
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch

__all__ = [
    "CASMismatch",
    "ListGatewayFunctionsFilter",
    "ListGatewayFunctionsPage",
    "IGatewayFunctionRepo",
]


@dataclass
class ListGatewayFunctionsFilter:
    app_id: str = ""
    include_inactive: bool = False
    skip: int = 0
    top: int = 50
    name_contains: str = ""


@dataclass
class ListGatewayFunctionsPage:
    items: list[GatewayFunction] = field(default_factory=list)
    total: int = 0


class IGatewayFunctionRepo(Protocol):
    def save(
        self, entity: GatewayFunction, *, expected_version: int | None = None
    ) -> None:
        """Upsert with CAS (see IGatewayAppRepo.save)."""

    def get_by_id(self, id: str) -> GatewayFunction | None: ...

    def get_by_app_and_name(self, app_id: str, name: str) -> GatewayFunction | None: ...

    def list_by_app(
        self, filters: ListGatewayFunctionsFilter
    ) -> ListGatewayFunctionsPage: ...

    def soft_delete(self, id: str) -> None: ...
