"""In-memory GatewayFunction repository (mock/local INFRA_MODE)."""
from __future__ import annotations

import copy

from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer2_application.repositories.gateway_function_repo_interface import (
    CASMismatch,
    IGatewayFunctionRepo,
    ListGatewayFunctionsFilter,
    ListGatewayFunctionsPage,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryGatewayFunctionRepo(AsyncRepoMixin, IGatewayFunctionRepo):
    def __init__(self) -> None:
        self._store: dict[str, GatewayFunction] = {}

    def save(
        self, entity: GatewayFunction, *, expected_version: int | None = None
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
        self._store[entity.id] = copy.deepcopy(entity)

    def get_by_id(self, id: str) -> GatewayFunction | None:
        stored = self._store.get(id)
        return copy.deepcopy(stored) if stored is not None else None

    def get_by_app_and_name(self, app_id: str, name: str) -> GatewayFunction | None:
        for entity in self._store.values():
            if entity.app_id == app_id and entity.name == name:
                return copy.deepcopy(entity)
        return None

    def list_by_app(
        self, filters: ListGatewayFunctionsFilter
    ) -> ListGatewayFunctionsPage:
        items = [e for e in self._store.values() if e.app_id == filters.app_id]
        if not filters.include_inactive:
            items = [e for e in items if e.is_active]
        if filters.name_contains:
            needle = filters.name_contains.lower()
            items = [e for e in items if needle in e.name.lower()]
        items.sort(key=lambda e: e.name)
        total = len(items)
        page = items[filters.skip : filters.skip + filters.top]
        return ListGatewayFunctionsPage(
            items=[copy.deepcopy(e) for e in page], total=total
        )

    def soft_delete(self, id: str) -> None:
        entity = self._store.get(id)
        if entity is None:
            return
        entity.is_active = False
        entity.version += 1
