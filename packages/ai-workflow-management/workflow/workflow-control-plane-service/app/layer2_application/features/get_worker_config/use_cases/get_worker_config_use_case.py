"""SA-2437 — read the persisted worker-instance config for a flow."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.entities.workflow_worker_config import WorkflowWorkerConfig
from app.layer2_application.interfaces.worker_config_repository_interface import (
    IWorkerConfigRepository,
)


@dataclass
class GetWorkerConfigCommand:
    workflow_id: str = ""


class GetWorkerConfigUseCase:
    def __init__(self, worker_config_repo: IWorkerConfigRepository, **kwargs: Any) -> None:
        self._repo = worker_config_repo

    async def execute(self, command: GetWorkerConfigCommand) -> WorkflowWorkerConfig | None:
        if not command.workflow_id:
            return None
        return self._repo.get(command.workflow_id)
