"""In-memory ``IWorkerConfigRepository`` (mock/local INFRA_MODE) — SA-2437."""
from __future__ import annotations

import threading

from app.layer1_domain.entities.workflow_worker_config import WorkflowWorkerConfig
from app.layer2_application.interfaces.worker_config_repository_interface import (
    IWorkerConfigRepository,
)
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryWorkerConfigRepository(AsyncRepoMixin, IWorkerConfigRepository):
    """Dict-backed, keyed by workflow_id. Thread-safe (FastAPI sync-route pool)."""

    def __init__(self) -> None:
        self._by_id: dict[str, WorkflowWorkerConfig] = {}
        self._lock = threading.Lock()

    def get(self, workflow_id: str) -> WorkflowWorkerConfig | None:
        with self._lock:
            return self._by_id.get(workflow_id)

    def upsert(self, config: WorkflowWorkerConfig) -> None:
        with self._lock:
            self._by_id[config.workflow_id] = config

    def delete(self, workflow_id: str) -> bool:
        with self._lock:
            return self._by_id.pop(workflow_id, None) is not None
