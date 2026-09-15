"""In-memory Environment repository (mock/local INFRA_MODE).

Entities are deep-copied on save and read so the store behaves like a SQL-backed
repo — a caller mutating ``entity`` after ``save()`` must not also mutate the
stored row, or CAS breaks. Mirrors ``MemoryGatewayAppRepo``.
"""
from __future__ import annotations

import copy

from app.layer1_domain.entities.environment import Environment
from app.layer2_application.repositories.environment_repo_interface import (
    CASMismatch,
    IEnvironmentRepo,
    ListEnvironmentsFilter,
    ListEnvironmentsPage,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryEnvironmentRepo(AsyncRepoMixin, IEnvironmentRepo):
    def __init__(self) -> None:
        self._store: dict[str, Environment] = {}

    def save(self, entity: Environment, *, expected_version: int | None = None) -> None:
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
        # Enforce the (tenant_id, section, name) natural-key uniqueness the way a
        # DB unique index would — a different id occupying the same triple is a conflict.
        for other in self._store.values():
            if other.id == entity.id:
                continue
            if (
                other.is_active
                and other.tenant_id == entity.tenant_id
                and other.section == entity.section
                and other.name == entity.name
            ):
                raise CASMismatch(
                    f"environment ({entity.tenant_id}/{entity.section}/{entity.name}) already exists"
                )
        self._store[entity.id] = copy.deepcopy(entity)

    def get_by_id(self, id: str) -> Environment | None:
        stored = self._store.get(id)
        return copy.deepcopy(stored) if stored is not None else None

    def get_by_natural_key(
        self, *, tenant_id: str, section: str, name: str
    ) -> Environment | None:
        for entity in self._store.values():
            if (
                entity.tenant_id == tenant_id
                and entity.section == section
                and entity.name == name
            ):
                return copy.deepcopy(entity)
        return None

    def list_paged(self, filters: ListEnvironmentsFilter) -> ListEnvironmentsPage:
        items = [e for e in self._store.values() if e.tenant_id == filters.tenant_id]
        if not filters.include_inactive:
            items = [e for e in items if e.is_active]
        if filters.section:
            items = [e for e in items if e.section == filters.section]
        if filters.name_contains:
            needle = filters.name_contains.lower()
            items = [e for e in items if needle in e.name.lower()]
        items.sort(key=lambda e: (e.section, e.name))
        total = len(items)
        page = items[filters.skip : filters.skip + filters.top]
        return ListEnvironmentsPage(items=[copy.deepcopy(e) for e in page], total=total)

    def soft_delete(self, id: str) -> None:
        entity = self._store.get(id)
        if entity is None:
            return
        entity.is_active = False
        entity.version += 1
