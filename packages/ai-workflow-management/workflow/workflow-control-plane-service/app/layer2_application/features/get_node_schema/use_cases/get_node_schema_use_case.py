"""Return the auto-form schema + versioning metadata for a node.

Serves ``GET /runs/{run_id}/nodes/{node_id}/schema``. The schema is
opaque to the backend; the handler that owns the node decides what to
return via its :class:`SchemaProvider` (see
``app/layer2_application/node_handlers/schema_providers.py``).
Non-PENDING handlers expose no provider — for those the use case
short-circuits with ``schema_source = "none"``.

Response fields:

- ``schema_version``: content hash; drives ``If-None-Match`` caching.
- ``schema_source``: ``"runtime"`` | ``"design_time"`` | ``"none"``.
- ``node_kind``: one of INPUT / HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD / …
- ``node_state``: coarse state derived from run context + latest task.
- ``node``: design-time node metadata (name / node_type / description /
  worker_type) — same contract as the ``_node`` block on centralized
  event envelopes (see ``CentralizedEventEnricher``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer1_domain.exceptions import UnsupportedNodeTypeError
from app.layer2_application.read.node_data_projection import (
    compute_node_state,
    find_latest_task_for_node,
)
from app.layer2_application.node_handlers.base import NodeHandlerRegistry
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository


@dataclass
class GetNodeSchemaCommand:
    run_id: str = ""
    node_id: str = ""
    task_id: Optional[str] = None


@dataclass
class GetNodeSchemaResult:
    schema: Any = field(default_factory=list)
    schema_version: str = "v0"
    schema_source: str = "design_time"
    node_kind: str = ""
    node_state: str = "PENDING"
    node: dict[str, Any] = field(default_factory=dict)

    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetNodeSchemaUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        handler_registry: Optional[NodeHandlerRegistry] = None,
        run_context_view_store: Optional[IRunContextViewStore] = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self._handler_registry = handler_registry
        self._view_store = run_context_view_store

    async def execute(self, command: GetNodeSchemaCommand) -> GetNodeSchemaResult:
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return self._error(ErrorCode.RUN_NOT_FOUND, {"run_id": command.run_id})

        workflow = await self.workflow_repo.aget_by_id(run.workflow_id)
        if not workflow:
            return self._error(
                ErrorCode.WORKFLOW_NOT_FOUND, {"workflow_id": run.workflow_id}
            )

        node = workflow.get_node(command.node_id) if hasattr(workflow, "get_node") else None
        if node is None:
            for n in getattr(workflow, "nodes", []) or []:
                if getattr(n, "id", "") == command.node_id:
                    node = n
                    break
        if node is None:
            return self._error(
                ErrorCode.NODE_NOT_FOUND,
                {"run_id": command.run_id, "node_id": command.node_id},
            )

        task = await self._resolve_task(
            run_id=command.run_id,
            node_id=command.node_id,
            task_id=command.task_id,
        )

        node_state = compute_node_state(run, command.node_id, task)
        node_kind_value = getattr(
            getattr(node, "node_type", None), "value", None,
        ) or str(getattr(node, "node_type", ""))
        # Same contract as the `_node` block on centralized event envelopes.
        node_info = {
            "name": getattr(node, "name", "") or "",
            "node_type": str(node_kind_value),
            "description": getattr(node, "description", "") or "",
            "worker_type": getattr(node, "worker_type", "") or "",
        }

        # The handler decides which provider drives /schema for this
        # node type. Non-PENDING handlers return None → schema_source
        # = "none". Unsupported node types — or test harnesses that
        # didn't wire a registry — are treated the same way.
        if self._handler_registry is None:
            return GetNodeSchemaResult(
                schema=[],
                schema_version="v0",
                schema_source="none",
                node_kind=str(node_kind_value),
                node_state=node_state,
                node=node_info,
            )
        try:
            handler = self._handler_registry.get(node.node_type)
        except UnsupportedNodeTypeError:
            return GetNodeSchemaResult(
                schema=[],
                schema_version="v0",
                schema_source="none",
                node_kind=str(node_kind_value),
                node_state=node_state,
                node=node_info,
            )

        provider = handler.get_schema_provider()
        if provider is None:
            return GetNodeSchemaResult(
                schema=[],
                schema_version="v0",
                schema_source="none",
                node_kind=str(node_kind_value),
                node_state=node_state,
                node=node_info,
            )

        view = self._view_store.get(command.run_id) if self._view_store else None
        schema, schema_version, schema_source = await provider.resolve(
            node=node, task=task, run=run, view=view,
        )

        return GetNodeSchemaResult(
            schema=schema,
            schema_version=schema_version,
            schema_source=schema_source,
            node_kind=str(node_kind_value),
            node_state=node_state,
            node=node_info,
        )

    # ------------------------------------------------------------------

    async def _resolve_task(
        self,
        *,
        run_id: str,
        node_id: str,
        task_id: Optional[str],
    ) -> Any:
        tasks = await self.task_repo.alist_by_run(run_id)
        if task_id:
            for t in tasks:
                if getattr(t, "id", "") == task_id and getattr(t, "node_id", "") == node_id:
                    return t
            return None
        return find_latest_task_for_node(tasks, node_id)

    @staticmethod
    def _error(code: ErrorCode, details: dict[str, Any]) -> GetNodeSchemaResult:
        return GetNodeSchemaResult(
            error=code.default_message,
            error_code=code.code,
            error_details=details,
        )
