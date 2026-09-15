"""In-memory EnvSectionSchema repository (mock/local INFRA_MODE).

Entities are deep-copied on save and read so the store behaves like a SQL-backed
repo. Mirrors ``MemoryEnvironmentRepo``: natural key ``(tenant_id, section)`` is
unique for ACTIVE rows only, CAS on VERSION.
"""
from __future__ import annotations

import copy

from app.layer1_domain.entities.env_section_schema import EnvSectionSchema
from app.layer2_application.repositories.env_section_schema_repo_interface import (
    CASMismatch,
    IEnvSectionSchemaRepo,
    ListEnvSectionSchemasFilter,
    ListEnvSectionSchemasPage,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryEnvSectionSchemaRepo(AsyncRepoMixin, IEnvSectionSchemaRepo):
    def __init__(self) -> None:
        self._store: dict[str, EnvSectionSchema] = {}

    def save(self, entity: EnvSectionSchema, *, expected_version: int | None = None) -> None:
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
        # Enforce (tenant_id, section) natural-key uniqueness for ACTIVE rows.
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
                    f"section schema ({entity.tenant_id}/{entity.section}) already exists"
                )
        self._store[entity.id] = copy.deepcopy(entity)

    def get_by_id(self, id: str) -> EnvSectionSchema | None:
        stored = self._store.get(id)
        return copy.deepcopy(stored) if stored is not None else None

    def get_by_natural_key(
        self, *, tenant_id: str, section: str
    ) -> EnvSectionSchema | None:
        for entity in self._store.values():
            if (
                entity.is_active
                and entity.tenant_id == tenant_id
                and entity.section == section
            ):
                return copy.deepcopy(entity)
        return None

    def list_paged(
        self, filters: ListEnvSectionSchemasFilter
    ) -> ListEnvSectionSchemasPage:
        items = [e for e in self._store.values() if e.tenant_id == filters.tenant_id]
        if not filters.include_inactive:
            items = [e for e in items if e.is_active]
        if filters.section:
            items = [e for e in items if e.section == filters.section]
        items.sort(key=lambda e: e.section)
        total = len(items)
        page = items[filters.skip : filters.skip + filters.top]
        return ListEnvSectionSchemasPage(
            items=[copy.deepcopy(e) for e in page], total=total
        )

    def soft_delete(self, id: str) -> None:
        entity = self._store.get(id)
        if entity is None:
            return
        entity.is_active = False
        entity.version += 1
