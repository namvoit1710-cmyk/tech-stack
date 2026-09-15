"""Scoped OData wrapper: query an artifact's rows with tenancy enforcement.

Serves ``GET /runs/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata``.
This is a thin pass-through to the file service, with three guarantees:

1. **Tenancy/authorization scope**: the ``artifact_id`` must exist in the
   node's ``__file_ref`` descriptor tree; if it doesn't, we return 404 so
   clients can't probe arbitrary file_ids via this endpoint.
2. **File-id resolution**: the caller never sees raw ``file_id``s — it uses
   ``artifact_id`` (the FE-authored dotted path). The backend resolves to the
   current working-copy file_id via ``find_artifact``.
3. **OData delegation**: the query string (``$filter``, ``$orderby``,
   ``$skip``, ``$top``, ``$count``, ``$select``, ``$expand``) is forwarded
   unmodified to the file service. Control-plane does not parse OData.

The ``__file_ref`` is resolved with the same precedence ``GET …/nodes/{id}/data``
and PATCH use: the working copy ``view.variables[node_id]`` (projector-backed,
authoritative once the node is edited) **falling back to** the task's frozen
``resolved_input``. Reading the run aggregate (the old behaviour) returned
``ARTIFACT_NOT_FOUND`` for every artifact, because post Phase-3b the projector/
view is authoritative and node outputs (e.g. an INPUT node's provisioned
artifacts) are view-only. The fallback matters for a freshly-pending INPUT node:
before any edit — and after a cold restart, where the working copy isn't
durably replayed from the event log — the data lives only on
``resolved_input``. The view store itself falls back to the aggregate for runs
with no projected events yet, so direct-repo test fixtures also resolve.

Task resolution: explicit ``?task_id=`` targets a specific iteration (and must
belong to the node); otherwise the latest task for the node is used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.layer1_domain.entities.data_reference import find_artifact, is_file_ref
from app.layer2_application.read.node_data_projection import find_latest_task_for_node
from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.file_service_client_interface import IFileServiceClient
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.task_repository_interface import ITaskRepository


@dataclass
class QueryNodeArtifactCommand:
    run_id: str = ""
    node_id: str = ""
    artifact_id: str = ""
    task_id: Optional[str] = None
    query_params: dict[str, str] = field(default_factory=dict)


@dataclass
class QueryNodeArtifactResult:
    file_id: str = ""
    response: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class QueryNodeArtifactUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_context_view_store: IRunContextViewStore,
        task_repo: ITaskRepository,
        file_service_client: IFileServiceClient,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_context_view_store = run_context_view_store
        self.task_repo = task_repo
        self._fs = file_service_client

    async def execute(
        self, command: QueryNodeArtifactCommand,
    ) -> QueryNodeArtifactResult:
        view = self.run_context_view_store.get(command.run_id)
        if not view:
            return self._error(ErrorCode.RUN_NOT_FOUND, {"run_id": command.run_id})

        # Resolve the task: an explicit ``task_id`` must belong to the node;
        # otherwise use the latest task for the node. The task carries the
        # frozen ``resolved_input`` used as the input-side fallback below.
        tasks = await self.task_repo.alist_by_run(command.run_id)
        if command.task_id:
            task = next(
                (t for t in tasks
                 if getattr(t, "id", "") == command.task_id
                 and getattr(t, "node_id", "") == command.node_id),
                None,
            )
            if task is None:
                return self._error(
                    ErrorCode.TASK_NOT_FOUND,
                    {"task_id": command.task_id, "node_id": command.node_id},
                )
        else:
            task = find_latest_task_for_node(tasks, command.node_id)

        # Locate the node's __file_ref with the same precedence GET /data and
        # PATCH use: the projector-backed working copy (authoritative once the
        # node is edited), falling back to the task's frozen ``resolved_input``.
        # The fallback is essential for a freshly-pending INPUT node — before
        # any edit, and after a cold restart where the working copy isn't
        # durably replayed from the event log, the provisioned data lives only
        # on ``resolved_input``.
        output_ref = view.variables.get(command.node_id)
        input_ref = getattr(task, "resolved_input", None) if task is not None else None
        file_ref = output_ref if is_file_ref(output_ref) else input_ref
        if not is_file_ref(file_ref):
            # Node not yet populated with any data — treat as 404 on artifact.
            return self._error(
                ErrorCode.ARTIFACT_NOT_FOUND,
                {
                    "run_id": command.run_id, "node_id": command.node_id,
                    "artifact_id": command.artifact_id,
                },
            )

        artifact = find_artifact(file_ref, command.artifact_id)
        if artifact is None:
            return self._error(
                ErrorCode.ARTIFACT_NOT_FOUND,
                {
                    "run_id": command.run_id, "node_id": command.node_id,
                    "artifact_id": command.artifact_id,
                },
            )

        file_id = artifact.get("file_id", "")
        if not file_id:
            return self._error(
                ErrorCode.ARTIFACT_NOT_FOUND,
                {
                    "reason": "artifact has no file_id yet",
                    "artifact_id": command.artifact_id,
                },
            )

        # Forward the OData query unmodified.
        response = await self._fs.query_file_data(file_id, command.query_params)
        return QueryNodeArtifactResult(file_id=file_id, response=response)

    @staticmethod
    def _error(code: ErrorCode, details: dict[str, Any]) -> QueryNodeArtifactResult:
        return QueryNodeArtifactResult(
            error=code.default_message,
            error_code=code.code,
            error_details=details,
        )
