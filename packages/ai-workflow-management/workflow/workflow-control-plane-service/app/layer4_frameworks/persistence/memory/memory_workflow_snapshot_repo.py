"""In-memory implementation of ``IWorkflowSnapshotRepository``."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from app.layer1_domain.entities.workflow_snapshot import WorkflowSnapshot
from app.layer2_application.repositories.workflow_snapshot_repository_interface import (
    IWorkflowSnapshotRepository,
)


from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryWorkflowSnapshotRepository(AsyncRepoMixin, IWorkflowSnapshotRepository):
    """Process-local snapshot store.

    The stored snapshot is a deep copy of the input so callers can't
    mutate a persisted definition by holding on to the same dict
    reference — matches the immutability guarantee the production HANA
    repo provides naturally.
    """

    def __init__(self) -> None:
        self._store: dict[str, WorkflowSnapshot] = {}

    def save(self, snapshot: WorkflowSnapshot) -> None:
        self._store[snapshot.id] = replace(
            snapshot, definition=deepcopy(snapshot.definition),
        )

    def get_by_id(self, snapshot_id: str) -> WorkflowSnapshot | None:
        stored = self._store.get(snapshot_id)
        if stored is None:
            return None
        # Return a fresh copy so callers mutating the dict don't poison
        # the store on the next save round-trip.
        return replace(stored, definition=deepcopy(stored.definition))

    def delete(self, snapshot_id: str) -> None:
        self._store.pop(snapshot_id, None)
