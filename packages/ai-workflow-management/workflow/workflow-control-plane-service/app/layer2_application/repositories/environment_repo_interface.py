from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.layer1_domain.entities.environment import Environment
# CASMismatch is shared across repos; reuse the gateway one so the update use
# cases map a single exception type to HTTP 409.
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch  # noqa: F401


@dataclass
class ListEnvironmentsFilter:
    tenant_id: str = "system"
    include_inactive: bool = False
    section: str = ""          # "" = all sections
    name_contains: str = ""
    skip: int = 0
    top: int = 100


@dataclass
class ListEnvironmentsPage:
    items: list[Environment] = field(default_factory=list)
    total: int = 0


class IEnvironmentRepo(Protocol):
    def save(self, entity: Environment, *, expected_version: int | None = None) -> None:
        """Upsert. When ``expected_version`` is set, raise CASMismatch on mismatch.

        On insert, ``expected_version`` must be ``None``. On update, the caller
        passes the version it read; the repo checks the stored row and persists
        the entity's already-bumped version atomically.
        """

    def get_by_id(self, id: str) -> Environment | None: ...

    def get_by_natural_key(
        self, *, tenant_id: str, section: str, name: str
    ) -> Environment | None:
        """Resolve the unique (tenant_id, section, name) triple, or None."""

    def list_paged(self, filters: ListEnvironmentsFilter) -> ListEnvironmentsPage: ...

    def soft_delete(self, id: str) -> None:
        """Mark ``is_active=False``; leave the row in place for audit."""
