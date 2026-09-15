"""SA-2437 — upsert (or clear) the persisted worker-instance config for a flow."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer1_domain.entities.workflow_worker_config import WorkflowWorkerConfig
from app.layer1_domain.value_objects.instance_selection import normalize_selection
from app.layer2_application.interfaces.worker_config_repository_interface import (
    IWorkerConfigRepository,
)


@dataclass
class SetWorkerConfigCommand:
    workflow_id: str = ""
    tenant_id: str = "system"
    mode: str = "WHOLE_FLOW"
    selection: dict[str, Any] = field(default_factory=dict)
    updated_by: str = "system"


@dataclass
class SetWorkerConfigResult:
    workflow_id: str = ""
    mode: str = ""
    selection: dict[str, Any] = field(default_factory=dict)
    cleared: bool = False
    error: str = ""


class SetWorkerConfigUseCase:
    def __init__(self, worker_config_repo: IWorkerConfigRepository, **kwargs: Any) -> None:
        self._repo = worker_config_repo

    async def execute(self, command: SetWorkerConfigCommand) -> SetWorkerConfigResult:
        if not command.workflow_id:
            return SetWorkerConfigResult(error="workflow_id is required")

        # Merge mode into the selection payload so normalize validates it, then
        # strip empties. An empty result clears the saved pin (delete the row).
        payload = dict(command.selection or {})
        if command.mode and "mode" not in payload:
            payload["mode"] = command.mode
        try:
            selection = normalize_selection(payload)
        except ValueError as exc:
            return SetWorkerConfigResult(workflow_id=command.workflow_id, error=str(exc))

        if not selection:
            self._repo.delete(command.workflow_id)
            return SetWorkerConfigResult(workflow_id=command.workflow_id, cleared=True)

        mode = selection.get("mode") or command.mode or "WHOLE_FLOW"
        config = WorkflowWorkerConfig(
            workflow_id=command.workflow_id,
            tenant_id=command.tenant_id or "system",
            mode=mode,
            selection={
                "by_worker_type": selection.get("by_worker_type", {}),
                "by_node": selection.get("by_node", {}),
            },
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=command.updated_by or "system",
        )
        self._repo.upsert(config)
        return SetWorkerConfigResult(
            workflow_id=config.workflow_id, mode=config.mode, selection=config.selection,
        )
