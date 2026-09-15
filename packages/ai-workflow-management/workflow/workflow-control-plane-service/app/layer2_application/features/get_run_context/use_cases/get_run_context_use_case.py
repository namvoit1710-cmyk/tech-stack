"""Return a summary of all context variable entries for a workflow run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer2_application.errors import ErrorCode


@dataclass
class GetRunContextCommand:
    run_id: str = ""


@dataclass
class ContextEntry:
    key: str = ""
    node_id: str | None = None
    node_name: str = ""
    node_type: str | None = None
    data_type: str = "inline"  # "inline" | "file_ref"
    file_id: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    updated_at: str | None = None


@dataclass
class GetRunContextResult:
    run_id: str = ""
    workflow_id: str = ""
    run_status: str = ""
    entries: list[ContextEntry] = field(default_factory=list)
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


def _extract_file_id(uri: str) -> str | None:
    """Best-effort extraction of a file_id from an output URI."""
    if not uri:
        return None
    # URI is typically the file_id itself or a path like "files/abc-123"
    parts = uri.rstrip("/").rsplit("/", 1)
    return parts[-1] if parts[-1] else None


def _classify_entry(value: Any) -> tuple[str, str | None]:
    """Return (data_type, file_id) for a context variable value."""
    if isinstance(value, dict):
        # New-style file-backed reference
        from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
        if value.get(FILE_REF_MARKER) is True:
            return "file_ref", value.get("file_id")
        # Legacy style: output.uri
        output = value.get("output")
        if isinstance(output, dict):
            uri = output.get("uri")
            if uri:
                return "file_ref", _extract_file_id(uri)
    return "inline", None


def _build_summary(value: Any, data_type: str) -> dict[str, Any]:
    """Build a lightweight summary dict for the context entry."""
    if isinstance(value, dict):
        # New-style file-backed reference
        from app.layer1_domain.entities.data_reference import FILE_REF_MARKER
        if value.get(FILE_REF_MARKER) is True:
            summary: dict[str, Any] = {
                "file_id": value.get("file_id", ""),
                "content_type": value.get("content_type", ""),
                "row_count": value.get("row_count", 0),
                "columns": value.get("columns", []),
                "artifact_count": len(value.get("artifacts", [])),
            }
            # Include extracted scalar values for quick display
            extracted = value.get("extracted", {})
            for path, entry in extracted.items():
                if isinstance(entry, dict) and "value" in entry:
                    summary[path] = entry["value"]
            return summary

    if data_type == "file_ref" and isinstance(value, dict):
        summary = {}
        output = value.get("output")
        if isinstance(output, dict):
            summary["uri"] = output.get("uri", "")
        metadata = value.get("metadata")
        if isinstance(metadata, dict):
            summary.update(metadata)
        return summary
    # Inline: return the full value (small by definition)
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


class GetRunContextUseCase:
    """Phase 4a: reads run state through ``IRunContextViewStore``.

    Still depends on ``IWorkflowRepository`` (for static workflow metadata —
    node definitions) and ``ITaskRepository`` (for ``completed_at`` timing).
    Only the live run aggregate has been replaced with the read-side view.
    """

    def __init__(
        self,
        logger: ILogger,
        run_context_view_store: IRunContextViewStore,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_context_view_store = run_context_view_store
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo

    async def execute(self, command: GetRunContextCommand) -> GetRunContextResult:
        view = self.run_context_view_store.get(command.run_id)
        if not view:
            return GetRunContextResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        workflow = await self.workflow_repo.aget_by_id(view.workflow_id)
        if not workflow:
            return GetRunContextResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": view.workflow_id},
            )

        # Canonical keys: node IDs + "input" — excludes name aliases
        canonical_keys = {n.id for n in workflow.nodes} | {"input"}

        # Build a map of node_id → latest completed_at from tasks
        all_tasks = await self.task_repo.alist_by_run(command.run_id)
        completed_at_map: dict[str, str | None] = {}
        for task in all_tasks:
            if task.status == TaskStatus.COMPLETED and task.completed_at:
                existing = completed_at_map.get(task.node_id)
                if not existing or task.completed_at > existing:
                    completed_at_map[task.node_id] = task.completed_at

        entries: list[ContextEntry] = []
        for key in canonical_keys:
            value = view.variables.get(key)
            if value is None:
                continue

            node = workflow.get_node(key) if key != "input" else None
            data_type, file_id = _classify_entry(value)
            summary = _build_summary(value, data_type)

            entries.append(ContextEntry(
                key=key,
                node_id=key if key != "input" else None,
                node_name=node.name if node else "Workflow Input",
                node_type=(
                    node.node_type.value
                    if node and hasattr(node.node_type, "value")
                    else str(node.node_type) if node else None
                ),
                data_type=data_type,
                file_id=file_id,
                summary=summary,
                updated_at=completed_at_map.get(key),
            ))

        return GetRunContextResult(
            run_id=command.run_id,
            workflow_id=view.workflow_id,
            run_status=view.status,
            entries=entries,
        )
