from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.layer1_domain.entities.gateway_app import GatewayApp


@dataclass
class ListGatewayAppsFilter:
    include_inactive: bool = False
    skip: int = 0
    top: int = 50
    name_contains: str = ""


@dataclass
class ListGatewayAppsPage:
    items: list[GatewayApp] = field(default_factory=list)
    total: int = 0


class CASMismatch(Exception):
    """Raised by ``save`` when ``expected_version`` does not match the stored row.

    The update use case maps this to HTTP 409 OPTIMISTIC_CONCURRENCY_CONFLICT.
    Shared by both gateway repos (the function repo imports it from here).
    """


class IGatewayAppRepo(Protocol):
    def save(self, entity: GatewayApp, *, expected_version: int | None = None) -> None:
        """Upsert. When ``expected_version`` is set, raise CASMismatch on mismatch.

        On insert, ``expected_version`` must be ``None``. On update, the caller
        passes the version it read; the repo checks the stored row and persists
        the entity's already-bumped version atomically.
        """

    def get_by_id(self, id: str) -> GatewayApp | None: ...

    def get_by_name(self, name: str) -> GatewayApp | None: ...

    def list_paged(self, filters: ListGatewayAppsFilter) -> ListGatewayAppsPage: ...

    def soft_delete(self, id: str) -> None:
        """Mark ``is_active=False``; leave the row in place for audit."""
