"""Get execution details for a specific node within a run."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.errors import ErrorCode
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.read.run_state import skipped_nodes_of
from app.layer2_application.interfaces.run_context_view_interface import (
    IRunContextViewStore,
)
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.repositories.task_repository_interface import ITaskRepository
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.read.node_status_aggregator import aggregate_node_status
from app.layer1_domain.value_objects.task_status import TaskStatus


@dataclass
class GetNodeExecutionCommand:
    run_id: str = ""
    node_id: str = ""


@dataclass
class ExecutionEntry:
    task_id: str = ""
    status: str = ""
    iteration_index: int | None = None
    resolved_input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    output_metadata: dict[str, Any] | None = None
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    editable: bool = False
    started_at: str | None = None
    completed_at: str | None = None


@dataclass
class GetNodeExecutionResult:
    run_id: str = ""
    node_id: str = ""
    node_name: str = ""
    node_type: str = ""
    status: str = ""
    skipped: bool = False
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    edit_mode: str = "none"
    data_mode: str = "json"
    executions: list[dict[str, Any]] = field(default_factory=list)
    context_output: dict[str, Any] | None = None
    input_binding: dict[str, Any] = field(default_factory=dict)
    output_binding: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetNodeExecutionUseCase:
    def __init__(
        self,
        logger: ILogger,
        run_repo: IRunRepository,
        task_repo: ITaskRepository,
        workflow_repo: IWorkflowRepository,
        run_context_view_store: IRunContextViewStore | None = None,
        **kwargs: Any,
    ) -> None:
        self.logger = logger
        self.run_repo = run_repo
        self.task_repo = task_repo
        self.workflow_repo = workflow_repo
        self.run_context_view_store = run_context_view_store

    async def execute(self, command: GetNodeExecutionCommand) -> GetNodeExecutionResult:
        # 1. Load run
        run = await self.run_repo.aget_by_id(command.run_id)
        if not run:
            return GetNodeExecutionResult(
                error=ErrorCode.RUN_NOT_FOUND.default_message,
                error_code=ErrorCode.RUN_NOT_FOUND.code,
                error_details={"run_id": command.run_id},
            )

        # 2. Load workflow
        workflow = await self.workflow_repo.aget_by_id(run.workflow_id)
        if not workflow:
            return GetNodeExecutionResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": run.workflow_id},
            )

        # 3. Get node
        node = workflow.get_node(command.node_id)
        if not node:
            return GetNodeExecutionResult(
                error=ErrorCode.NODE_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_NOT_FOUND.code,
                error_details={"node_id": command.node_id},
            )

        # 4. Get tasks for this node, sorted by iteration_index
        all_tasks = await self.task_repo.alist_by_run(command.run_id)
        node_tasks = sorted(
            [t for t in all_tasks if t.node_id == command.node_id],
            key=lambda t: (t.scope_stack[-1].iter if t.scope_stack else 0),
        )

        # 5. Resolve node definition metadata
        node_def = node.definition_snapshot or {}
        input_schema = node_def.get("input_schema", [])
        if not input_schema and node_tasks:
            input_schema = (node_tasks[0].output_metadata or {}).get("input_schema", [])
        edit_mode = node_def.get("edit_mode", "none")
        data_mode = node_def.get("data_mode", "json")

        # 6. Context output. The aggregate's ``variables`` is stale for
        # event-authoritative outputs (e.g. a failed node's failure_info is
        # written via NodeOutputProduced, not the aggregate), so fall back
        # to the view store — the same source GET /data reads.
        context_output = run.context.variables.get(command.node_id)
        if context_output is None and self.run_context_view_store is not None:
            view = self.run_context_view_store.get(command.run_id)
            if view is not None:
                context_output = view.variables.get(command.node_id)

        # 7. Skipped check
        skipped_set = skipped_nodes_of(
            command.run_id, self.run_context_view_store, run,
        )
        skipped = command.node_id in skipped_set

        # 8. Build executions list
        executions = []
        for task in node_tasks:
            meta = task.output_metadata or {}
            executions.append({
                "task_id": task.id,
                "status": task.status.value if isinstance(task.status, TaskStatus) else str(task.status),
                "iteration_index": task.scope_stack[-1].iter if task.scope_stack else None,
                "resolved_input": task.resolved_input,
                # Omit the file-pointer envelope when there is no stored output
                # reference (inline-output workers leave it empty) — surfacing
                # ``{"uri": ""}`` was noise. ``None`` signals "no file ref".
                "output": {"uri": task.output_ref} if task.output_ref else None,
                "output_metadata": task.output_metadata,
                "error": task.error,
                # Surface the worker-reported error code (stashed in
                # output_metadata by FailTaskUseCase) on failed executions —
                # the full error body lives in ``context_output`` (the
                # failure_info file-ref written via NodeOutputProduced).
                "error_code": meta.get("error_code", ""),
                "editable": task.status == TaskStatus.PENDING,
                "started_at": task.started_at,
                "completed_at": task.completed_at,
            })

        # 9. Aggregate top-level status (shared rule with bulk status endpoint)
        status = aggregate_node_status(
            command.node_id,
            node_tasks,
            skipped_set,
            run.status,
        )

        return GetNodeExecutionResult(
            run_id=command.run_id,
            node_id=command.node_id,
            node_name=node.name,
            node_type=node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
            status=status,
            skipped=skipped,
            input_schema=input_schema,
            edit_mode=edit_mode,
            data_mode=data_mode,
            executions=executions,
            context_output=context_output,
            input_binding=node.input_binding,
            output_binding=node.output_binding,
        )
