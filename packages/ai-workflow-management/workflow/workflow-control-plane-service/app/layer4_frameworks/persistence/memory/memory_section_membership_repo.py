"""In-memory ISectionMembershipRepo (mock/local INFRA_MODE) — SA-2605.

Deep-copies on save/read so the store behaves like a SQL-backed repo. Enforces one
ACTIVE membership row per (tenant_id, section). Mirrors MemoryWorkflowEnvironmentRepo.
"""
from __future__ import annotations

import copy

from app.layer1_domain.entities.section_membership import SectionMembership
from app.layer2_application.repositories.section_membership_repo_interface import (
    CASMismatch,
    ISectionMembershipRepo,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemorySectionMembershipRepo(AsyncRepoMixin, ISectionMembershipRepo):
    def __init__(self) -> None:
        self._store: dict[str, SectionMembership] = {}

    def save(
        self,
        entity: SectionMembership,
        *,
        expected_version: int | None = None,
    ) -> None:
        existing = self._store.get(entity.id)
        if expected_version is not None:
            if existing is None:
                raise CASMismatch(
                    f"expected_version={expected_version} but no row exists for id={entity.id}"
                )
            if existing.version != expected_version:
                raise CASMismatch(
                    f"expected_version={expected_version} but stored version={existing.version}"
                )
        # One ACTIVE membership row per (tenant_id, section).
        for other in self._store.values():
            if other.id == entity.id:
                continue
            if (
                other.is_active
                and entity.is_active
                and other.tenant_id == entity.tenant_id
                and other.section == entity.section
            ):
                raise CASMismatch(
                    f"section {entity.section} already has a membership row"
                )
        self._store[entity.id] = copy.deepcopy(entity)

    def get_by_section(
        self, *, tenant_id: str, section: str
    ) -> SectionMembership | None:
        for entity in self._store.values():
            if (
                entity.is_active
                and entity.tenant_id == tenant_id
                and entity.section == section
            ):
                return copy.deepcopy(entity)
        return None

    def list_all(self, *, tenant_id: str) -> list[SectionMembership]:
        out = [
            copy.deepcopy(e)
            for e in self._store.values()
            if e.is_active and e.tenant_id == tenant_id
        ]
        out.sort(key=lambda e: e.section)
        return out

    def list_sections_for_environment(
        self, *, tenant_id: str, environment_id: str
    ) -> list[str]:
        out = [
            e.section
            for e in self._store.values()
            if e.is_active
            and e.tenant_id == tenant_id
            and environment_id in e.environment_ids
        ]
        out.sort()
        return out

    def soft_delete_by_section(self, *, tenant_id: str, section: str) -> None:
        for entity in self._store.values():
            if (
                entity.is_active
                and entity.tenant_id == tenant_id
                and entity.section == section
            ):
                entity.is_active = False
                entity.version += 1
