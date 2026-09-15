from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.section_membership import SectionMembership
# CASMismatch is shared across repos; reuse the gateway one so update use cases
# map a single exception type to HTTP 409.
from app.layer2_application.repositories.gateway_app_repo_interface import CASMismatch  # noqa: F401


class ISectionMembershipRepo(Protocol):
    """Persistence for the Section↔Environment many-to-many (SA-2605).

    One ACTIVE row per (tenant_id, section) holding the section's environment ids.
    An environment id may appear under several sections; a section may hold many
    environments.
    """

    def save(
        self,
        entity: SectionMembership,
        *,
        expected_version: int | None = None,
    ) -> None:
        """Upsert. When ``expected_version`` is set, raise CASMismatch on mismatch."""

    def get_by_section(
        self, *, tenant_id: str, section: str
    ) -> SectionMembership | None:
        """The single ACTIVE membership row for a section, or None."""

    def list_all(self, *, tenant_id: str) -> list[SectionMembership]:
        """Every ACTIVE membership row for the tenant."""

    def list_sections_for_environment(
        self, *, tenant_id: str, environment_id: str
    ) -> list[str]:
        """Reverse lookup: the sections an environment currently belongs to."""

    def soft_delete_by_section(self, *, tenant_id: str, section: str) -> None:
        """Deactivate a section's membership row (e.g. the section is deleted)."""
