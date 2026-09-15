"""SA-2437 — per-workflow worker-config repository contract."""
from typing import Protocol

from app.layer1_domain.entities.workflow_worker_config import WorkflowWorkerConfig


class IWorkerConfigRepository(Protocol):
    """One row per flow (workflow) id. ``get`` returns None when unset."""

    def get(self, workflow_id: str) -> WorkflowWorkerConfig | None: ...
    def upsert(self, config: WorkflowWorkerConfig) -> None: ...
    def delete(self, workflow_id: str) -> bool: ...
