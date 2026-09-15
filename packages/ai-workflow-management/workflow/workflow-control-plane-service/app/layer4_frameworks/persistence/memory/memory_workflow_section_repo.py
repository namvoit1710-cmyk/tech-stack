"""In-memory IWorkflowSectionRepo (mock/local INFRA_MODE) — SA-2605.

Deep-copies on save/read so the store behaves like a SQL-backed repo. Enforces one
ACTIVE binding per (tenant_id, workflow_id) the way a DB unique index on active rows
would. Mirrors MemoryWorkflowEnvironmentRepo.
"""
from __future__ import annotations

import copy

from app.layer1_domain.entities.workflow_section import WorkflowSection
from app.layer2_application.repositories.workflow_section_repo_interface import (
    CASMismatch,
    IWorkflowSectionRepo,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryWorkflowSectionRepo(AsyncRepoMixin, IWorkflowSectionRepo):
    def __init__(self) -> None:
        self._store: dict[str, WorkflowSection] = {}

    def save(
        self,
        entity: WorkflowSection,
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
        # One ACTIVE binding per (tenant_id, workflow_id).
        for other in self._store.values():
            if other.id == entity.id:
                continue
            if (
                other.is_active
                and entity.is_active
                and other.tenant_id == entity.tenant_id
                and other.workflow_id == entity.workflow_id
            ):
                raise CASMismatch(
                    f"workflow {entity.workflow_id} already has a section binding"
                )
        self._store[entity.id] = copy.deepcopy(entity)

    def get_by_workflow(
        self, *, tenant_id: str, workflow_id: str
    ) -> WorkflowSection | None:
        for entity in self._store.values():
            if (
                entity.is_active
                and entity.tenant_id == tenant_id
                and entity.workflow_id == workflow_id
            ):
                return copy.deepcopy(entity)
        return None

    def list_by_section(
        self, *, tenant_id: str, section: str
    ) -> list[WorkflowSection]:
        out = [
            copy.deepcopy(e)
            for e in self._store.values()
            if e.is_active and e.tenant_id == tenant_id and e.section == section
        ]
        out.sort(key=lambda e: e.workflow_id)
        return out

    def soft_delete_by_workflow(self, *, tenant_id: str, workflow_id: str) -> None:
        for entity in self._store.values():
            if (
                entity.is_active
                and entity.tenant_id == tenant_id
                and entity.workflow_id == workflow_id
            ):
                entity.is_active = False
                entity.version += 1

    def clear_section(self, *, tenant_id: str, section: str, updated_by: str) -> int:
        n = 0
        for entity in self._store.values():
            if (
                entity.is_active
                and entity.tenant_id == tenant_id
                and entity.section == section
            ):
                entity.is_active = False
                entity.version += 1
                entity.updated_by = updated_by
                n += 1
        return n
