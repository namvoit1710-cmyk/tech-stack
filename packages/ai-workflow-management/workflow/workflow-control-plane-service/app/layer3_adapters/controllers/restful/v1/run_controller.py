import asyncio
import logging
import dataclasses

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel
from typing import Annotated, Any, Optional

from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseOutputDto
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response, result_or_error
from app.layer2_application.features.start_run.use_cases.start_run_use_case import StartRunUseCase, StartRunCommand
from app.layer2_application.features.complete_task.use_cases.complete_task_use_case import CompleteTaskUseCase, CompleteTaskCommand
from app.layer2_application.features.fail_task.use_cases.fail_task_use_case import FailTaskUseCase, FailTaskCommand
from app.layer2_application.features.cancel_run.use_cases.cancel_run_use_case import CancelRunUseCase, CancelRunCommand
from app.layer2_application.features.pause_run.use_cases.pause_run_use_case import PauseRunUseCase, PauseRunCommand
from app.layer2_application.features.resume_run.use_cases.resume_run_use_case import ResumeRunUseCase, ResumeRunCommand
from app.layer2_application.features.replay_pending_requests.use_cases.replay_pending_requests_use_case import (
    ReplayPendingRequestsUseCase,
    ReplayPendingRequestsCommand,
)
from app.layer2_application.features.get_run_status.use_cases.get_run_status_use_case import GetRunStatusUseCase, GetRunStatusCommand
from app.layer2_application.features.get_task.use_cases.get_task_use_case import GetTaskUseCase, GetTaskCommand
from app.layer2_application.features.get_node_execution.use_cases.get_node_execution_use_case import GetNodeExecutionUseCase, GetNodeExecutionCommand
from app.layer2_application.features.rerun_task.use_cases.rerun_task_use_case import RerunTaskUseCase, RerunTaskCommand
from app.layer2_application.features.get_run_context.use_cases.get_run_context_use_case import GetRunContextUseCase, GetRunContextCommand
from app.layer2_application.features.get_variable_suggestions.use_cases.get_variable_suggestions_use_case import GetVariableSuggestionsUseCase, GetVariableSuggestionsCommand
from app.layer2_application.features.get_node_output.use_cases.get_node_output_use_case import GetNodeOutputUseCase, GetNodeOutputCommand
from app.layer2_application.features.list_runs.use_cases.list_runs_use_case import ListRunsUseCase, ListRunsCommand
from app.layer2_application.features.get_node_schema.use_cases.get_node_schema_use_case import GetNodeSchemaUseCase, GetNodeSchemaCommand
from app.layer2_application.features.get_node_data.use_cases.get_node_data_use_case import GetNodeDataUseCase, GetNodeDataCommand
from app.layer2_application.features.get_run_artifacts.use_cases.get_run_artifacts_use_case import GetRunArtifactsUseCase, GetRunArtifactsCommand
from app.layer2_application.features.edit_node_data.use_cases.edit_node_data_use_case import EditNodeDataUseCase, EditNodeDataCommand
from app.layer2_application.features.submit_node.use_cases.submit_node_use_case import SubmitNodeUseCase, SubmitNodeCommand
from app.layer2_application.features.rerun_run.use_cases.rerun_run_use_case import RerunRunUseCase, RerunRunCommand
from app.layer2_application.features.resolve_variable_paths.use_cases.resolve_variable_paths_use_case import ResolveVariablePathsUseCase, ResolveVariablePathsCommand
from app.layer2_application.features.query_node_artifact.use_cases.query_node_artifact_use_case import QueryNodeArtifactUseCase, QueryNodeArtifactCommand
from app.layer2_application.features.trigger_node.use_cases.trigger_node_use_case import TriggerNodeUseCase, TriggerNodeCommand
from app.layer2_application.features.list_run_node_statuses.use_cases.list_run_node_statuses_use_case import ListRunNodeStatusesUseCase, ListRunNodeStatusesCommand
from app.layer2_application.features.list_run_children.use_cases.list_run_children_use_case import ListRunChildrenUseCase, ListRunChildrenCommand
from app.layer2_application.features.get_run_execution_log.use_cases.get_run_execution_log_use_case import GetRunExecutionLogUseCase, GetRunExecutionLogCommand
from app.layer2_application.features.replay_debugger.use_cases.replay_debugger_use_case import ReplayDebuggerUseCase
from app.layer2_application.interfaces.orchestration_queue_interface import EditNodeDataAction
from app.layer1_domain.value_objects.node_input_etag import NodeInputEtag

logger = logging.getLogger(__name__)


def _etag_not_modified(if_none_match: str | None, current_etag: str) -> bool:
    """Return True when the client's If-None-Match header matches the
    current ETag (weak comparison — weak and strong forms both accepted)."""
    if not if_none_match:
        return False
    return NodeInputEtag.matches_if_match(if_none_match, current_etag)


# ── Request DTOs ──────────────────────────────────────────────────


class StartRunRequest(BaseModel):
    workflow_id: str
    input_data: dict[str, Any] = {}
    tenant_id: str = "system"
    correlation_id: str = ""
    callback_topic: str = ""
    predefined_run_id: Optional[str] = None
    skip_validation: bool = True
    execution_mode: str = "auto"  # "auto" | "manual"
    # Per-invocation custom variable overrides exposed to expressions
    # as ``{{$variable.X}}``. Merged on top of ``Workflow.variables``
    # defaults at scope-build time.
    user_variables: dict[str, Any] = {}
    # Free-form caller metadata echoed back onto every outbound external
    # message (centralized topic, realtime, completion callback, task dispatch).
    metadata: dict[str, Any] = {}
    # Runtime credential map: name -> {type, config}. Stored encrypted under
    # the reserved key "__credentials" in RunContext; never echoed back to the
    # caller or exposed in variable suggestions samples.
    runtime_credentials: dict[str, dict] = {}
    # SA-2365: the environment selected for this run. Its non-secret variables
    # are injected as the ``$env`` source so node fields resolve {{$env.KEY}}.
    environment_id: str = ""
    # SA-2424: optional per-node worker-instance pin (debug routing).
    # {"mode": "WHOLE_FLOW"|"PER_NODE", "by_worker_type": {...}, "by_node": {...}}.
    # Omit / {} = no pin (normal freshest-healthy selection).
    instance_selection: dict[str, Any] = {}


class CompleteTaskRequest(BaseModel):
    status: str = "COMPLETED"
    output_reference: str = ""
    output_metadata: dict[str, Any] | None = None
    idempotency_key: str = ""


class FailTaskRequest(BaseModel):
    error: dict[str, Any] = {}
    outputs: dict[str, Any] = {}


class RerunTaskRequest(BaseModel):
    new_input: dict[str, Any] | None = None


class ResumeRunRequest(BaseModel):
    # Optional overrides applied to every PENDING task resumed by the run.
    updated_input: dict[str, Any] = {}


# ── Response DTOs ─────────────────────────────────────────────────


class RunOutputDto(BaseOutputDto):
    id: str
    workflow_id: str
    status: str
    started_at: str
    completed_at: str | None = None
    error: str | None = None
    output_data: dict | None = None
    execution_mode: str = "auto"


# ── Dependency helpers ────────────────────────────────────────────


def _get_list_runs(request: Request) -> ListRunsUseCase:
    return request.app.state.container["list_runs"]


def _get_start_run(request: Request) -> StartRunUseCase:
    return request.app.state.container["start_run"]


def _get_get_run_status(request: Request) -> GetRunStatusUseCase:
    return request.app.state.container["get_run_status"]


def _get_cancel_run(request: Request) -> CancelRunUseCase:
    return request.app.state.container["cancel_run"]


def _get_pause_run(request: Request) -> PauseRunUseCase:
    return request.app.state.container["pause_run"]


def _get_resume_run(request: Request) -> ResumeRunUseCase:
    return request.app.state.container["resume_run"]


def _get_replay_pending_requests(request: Request) -> ReplayPendingRequestsUseCase:
    return request.app.state.container["replay_pending_requests"]


def _get_complete_task(request: Request) -> CompleteTaskUseCase:
    return request.app.state.container["complete_task"]


def _get_fail_task(request: Request) -> FailTaskUseCase:
    return request.app.state.container["fail_task"]


def _get_get_task(request: Request) -> GetTaskUseCase:
    return request.app.state.container["get_task"]


def _get_get_node_execution(request: Request) -> GetNodeExecutionUseCase:
    return request.app.state.container["get_node_execution"]


def _get_rerun_task(request: Request) -> RerunTaskUseCase:
    return request.app.state.container["rerun_task"]


def _get_get_run_context(request: Request) -> GetRunContextUseCase:
    return request.app.state.container["get_run_context"]


def _get_get_variable_suggestions(request: Request) -> GetVariableSuggestionsUseCase:
    return request.app.state.container["get_variable_suggestions"]


def _get_get_node_output(request: Request) -> GetNodeOutputUseCase:
    return request.app.state.container["get_node_output"]


def _get_get_node_schema(request: Request) -> GetNodeSchemaUseCase:
    return request.app.state.container["get_node_schema"]


def _get_get_node_data(request: Request) -> GetNodeDataUseCase:
    return request.app.state.container["get_node_data"]


def _get_get_run_artifacts(request: Request) -> GetRunArtifactsUseCase:
    return request.app.state.container["get_run_artifacts"]


def _get_edit_node_data(request: Request) -> EditNodeDataUseCase:
    return request.app.state.container["edit_node_data"]


def _get_submit_node(request: Request) -> SubmitNodeUseCase:
    return request.app.state.container["submit_node"]


def _get_rerun_run(request: Request) -> RerunRunUseCase:
    return request.app.state.container["rerun_run"]


def _get_resolve_variable_paths(request: Request) -> ResolveVariablePathsUseCase:
    return request.app.state.container["resolve_variable_paths"]


def _get_query_node_artifact(request: Request) -> QueryNodeArtifactUseCase:
    return request.app.state.container["query_node_artifact"]


def _get_trigger_node(request: Request) -> TriggerNodeUseCase:
    return request.app.state.container["trigger_node"]


def _get_list_run_node_statuses(request: Request) -> ListRunNodeStatusesUseCase:
    return request.app.state.container["list_run_node_statuses"]


def _get_list_run_children(request: Request) -> ListRunChildrenUseCase:
    return request.app.state.container["list_run_children"]


def _get_get_run_execution_log(request: Request) -> GetRunExecutionLogUseCase:
    return request.app.state.container["get_run_execution_log"]


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


def _check_error(result: Any) -> Any:
    """Return an error response if the result has an error, otherwise None."""
    error = getattr(result, "error", None) or ""
    if not error:
        data = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else result
        error = data.get("error", "") if isinstance(data, dict) else ""
    if error:
        error_code_str = getattr(result, "error_code", "")
        details = getattr(result, "error_details", None) or None
        if error_code_str:
            from app.layer2_application.errors import ErrorCode
            ec = ErrorCode.from_code(error_code_str)
            if ec:
                return error_response(ec.code, error, status_code=ec.http_status, details=details)
        code = "NOT_FOUND" if "not found" in error.lower() else "BAD_REQUEST"
        status = 404 if code == "NOT_FOUND" else 400
        return error_response(code, error, status_code=status, details=details)
    return None


# ── Unit C/D/E/F: typed DTOs + shared OpenAPI response table ─────────
# DTOs live in dtos/node_data_dtos.py (extracted in Unit F).

from app.layer3_adapters.controllers.restful.v1.dtos.api_envelope import (
    ApiEnvelope, ApiErrorPayload,
)
from app.layer3_adapters.controllers.restful.v1.dtos.node_data_dtos import (
    GetNodeDataResponse,
    GetNodeSchemaResponse,
    GetRunArtifactsResponse,
    PatchNodeDataRequest,
    PatchNodeDataResponse,
    RerunRunRequestBody,
    RerunRunResponse,
    ResolvePathsRequestBody,
    ResolvePathsResponse,
    ScopedODataResponse,
    SubmitNodeResponse,
)

IfMatch = Annotated[str | None, Header(alias="If-Match")]

# Shared OpenAPI response table. Routes compose this with their own success
# and method-specific error codes. Uses ``responses={}`` (OpenAPI-only), NOT
# ``response_model=``, because the ResponseWrapperMiddleware wraps the body
# after the handler returns — using response_model here would make FastAPI
# try to validate the unwrapped return against the envelope shape and fail.
_ERR = {"model": ApiEnvelope[ApiErrorPayload]}


def _ok(model: type) -> dict:
    """Build a ``responses=`` dict entry for a 200 response wrapping ``model``."""
    return {"model": ApiEnvelope[model]}


def _err_responses(*codes: int) -> dict:
    """Standard error-response dict keyed by HTTP status code."""
    return {code: _ERR for code in codes}


@router.get("")
async def list_runs(
    request: Request,
    workflow_id: str | None = None,
    status: str | None = None,
    tenant_id: str | None = None,
    is_test: bool | None = None,
    roots_only: bool = False,
    usecase: ListRunsUseCase = Depends(_get_list_runs),
) -> Any:
    from app.layer3_adapters.controllers.restful.v1.dtos.odata_params import (
        parse_odata_params, parse_odata_filter, odata_page_response,
    )
    params = parse_odata_params(request)
    filters = parse_odata_filter(params.filter)

    # $filter takes priority over individual query params
    effective_workflow_id = filters.eq.get("workflow_id") or workflow_id
    status_raw = filters.eq.get("status") or status
    effective_tenant_id = filters.eq.get("tenant_id") or tenant_id

    if "is_test" in filters.eq:
        effective_is_test = filters.eq["is_test"].lower() == "true"
    else:
        effective_is_test = is_test

    statuses = [s.strip() for s in status_raw.split(",") if s.strip()] if status_raw else []
    # Strip keys absorbed by the legacy explicit fields so the new generic
    # eq dict doesn't double-filter them downstream.
    _legacy_eq_keys = ("workflow_id", "status", "tenant_id", "is_test")
    extra_eq = {k: v for k, v in filters.eq.items() if k not in _legacy_eq_keys}
    command = ListRunsCommand(
        workflow_id=effective_workflow_id,
        statuses=statuses,
        tenant_id=effective_tenant_id,
        is_test=effective_is_test,
        roots_only=roots_only,
        offset=params.skip,
        limit=params.top,
        eq=extra_eq,
        ne=filters.ne,
        gt=filters.gt,
        ge=filters.ge,
        lt=filters.lt,
        le=filters.le,
        contains=filters.contains,
    )
    result = await usecase.execute(command)
    return odata_page_response(result.items, result.total, params)


@router.post("")
async def start_run(
    request: Request,
    payload: StartRunRequest,
    usecase: StartRunUseCase = Depends(_get_start_run),
) -> Any:
    command = StartRunCommand(**payload.model_dump())
    # SA-2558: attribute the run to the server-verified caller (BFF session /
    # Bearer JWT), overriding anything the body might claim — never trust
    # payload for "who". StartRunRequest has no such field, so this is the only
    # source of TRIGGERED_BY.
    from app.layer3_adapters.controllers.restful.v1.auth_dependencies import (
        resolve_actor, actor_label,
    )
    command.triggered_by = actor_label(resolve_actor(request))
    result = await usecase.execute(command)
    if not result.id:
        # Surface the use case's error_code as the HTTP status (e.g. SA-2605
        # ENVIRONMENT_NOT_IN_SECTION → 422) instead of a blanket 400.
        from app.layer2_application.errors import ErrorCode
        error_code_str = getattr(result, "error_code", "") or ""
        details = getattr(result, "error_details", {}) or {}
        ec = ErrorCode.from_code(error_code_str) if error_code_str else None
        if ec is not None:
            return error_response(
                ec.code, result.error, status_code=ec.http_status, details=details
            )
        return error_response("BAD_REQUEST", result.error, details=details)
    return RunOutputDto.from_dataclass(result)


@router.get("/{run_id}")
async def get_run_status(
    run_id: str,
    usecase: GetRunStatusUseCase = Depends(_get_get_run_status),
) -> Any:
    command = GetRunStatusCommand(run_id=run_id)
    result = await usecase.execute(command)
    return RunOutputDto.from_dataclass(result)


@router.post("/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    usecase: CancelRunUseCase = Depends(_get_cancel_run),
) -> Any:
    command = CancelRunCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.post("/{run_id}/pause")
async def pause_run(
    run_id: str,
    usecase: PauseRunUseCase = Depends(_get_pause_run),
) -> Any:
    command = PauseRunCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: str,
    payload: ResumeRunRequest | None = None,
    usecase: ResumeRunUseCase = Depends(_get_resume_run),
) -> Any:
    """Re-trigger a run by run_id alone.

    Handles two states (and the combination of both): a PAUSED run (flips to
    RUNNING and re-dispatches stalled READY tasks), and any run with PENDING
    tasks waiting at interactive nodes (INPUT / HUMAN_ACTION / DATA_EDIT /
    FILE_UPLOAD), including inside nested sub-workflows. Optional body
    ``{updated_input: {...}}`` is applied to every PENDING task that gets
    resumed.
    """
    command = ResumeRunCommand(
        run_id=run_id,
        updated_input=(payload.updated_input if payload else {}) or {},
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.post("/{run_id}/replay-pending-requests")
async def replay_pending_requests(
    run_id: str,
    usecase: ReplayPendingRequestsUseCase = Depends(_get_replay_pending_requests),
) -> Any:
    """Re-fire ``*.requested`` events for every PENDING interactive task on a run.

    For an external system that reconnects to a run waiting at an INPUT /
    HUMAN_ACTION / DATA_EDIT / FILE_UPLOAD node, this cancels the old PENDING
    task (marked ``reason: "replayed"``) and re-runs the node handler from
    scratch. The handler creates a new PENDING task (new task id, fresh
    schema if upstream data changed) and emits a fresh ``input.requested``
    (or peer) event on Socket.IO. Walks descendant sub-workflow runs too.
    """
    command = ReplayPendingRequestsCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get("/{run_id}/nodes/status")
async def list_run_node_statuses(
    run_id: str,
    usecase: ListRunNodeStatusesUseCase = Depends(_get_list_run_node_statuses),
) -> Any:
    """Lightweight bulk per-node status snapshot for a run.

    Returns one entry per node in the workflow definition with status, timing,
    a short error string, and ``child_run_id`` when the node spawned a sub-workflow.
    Frontend uses this to drive the canvas and lazy-loads child runs by calling
    the same endpoint with the ``child_run_id``.
    """
    command = ListRunNodeStatusesCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get("/{run_id}/children")
async def list_run_children(
    run_id: str,
    usecase: ListRunChildrenUseCase = Depends(_get_list_run_children),
) -> Any:
    """List direct sub-workflow runs spawned by this run."""
    command = ListRunChildrenCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get(
    "/{run_id}/execution-log",
    summary="Full execution event log for a run",
    description=(
        "Returns the complete, ordered stream of domain events recorded for "
        "the run during execution, plus a `summary` block (status + event / "
        "node counts). By default nested sub-workflow runs are folded in "
        "(`include_children=false` for the selected run alone). Supports "
        "filtering by `event_type` and `node_id`, incremental fetch via "
        "`since_sequence`, and `limit`/`offset` pagination."
    ),
    tags=["execution-log"],
    responses={404: _ERR},
)
async def get_run_execution_log(
    run_id: str,
    event_type: str | None = None,
    node_id: str | None = None,
    since_sequence: int | None = None,
    limit: int | None = None,
    offset: int = 0,
    include_children: bool = True,
    usecase: GetRunExecutionLogUseCase = Depends(_get_get_run_execution_log),
) -> Any:
    command = GetRunExecutionLogCommand(
        run_id=run_id,
        event_type=event_type,
        node_id=node_id,
        since_sequence=since_sequence,
        limit=limit,
        offset=offset,
        include_children=include_children,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get("/{run_id}/nodes/{node_id}/execution")
async def get_node_execution(
    run_id: str,
    node_id: str,
    usecase: GetNodeExecutionUseCase = Depends(_get_get_node_execution),
) -> Any:
    command = GetNodeExecutionCommand(run_id=run_id, node_id=node_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get("/{run_id}/tasks/{task_id}")
async def get_task(
    run_id: str,
    task_id: str,
    usecase: GetTaskUseCase = Depends(_get_get_task),
) -> Any:
    command = GetTaskCommand(run_id=run_id, task_id=task_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.post("/{run_id}/tasks/{task_id}/complete")
async def complete_task(
    run_id: str,
    task_id: str,
    payload: CompleteTaskRequest,
    usecase: CompleteTaskUseCase = Depends(_get_complete_task),
) -> Any:
    data = payload.model_dump()
    if data.get("output_metadata") is None:
        data["output_metadata"] = {}
    command = CompleteTaskCommand(
        run_id=run_id,
        task_id=task_id,
        **data,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.post("/{run_id}/tasks/{task_id}/fail")
async def fail_task(
    run_id: str,
    task_id: str,
    payload: FailTaskRequest,
    usecase: FailTaskUseCase = Depends(_get_fail_task),
) -> Any:
    command = FailTaskCommand(
        run_id=run_id,
        task_id=task_id,
        **payload.model_dump(),
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.post("/{run_id}/tasks/{task_id}/rerun")
async def rerun_task(
    run_id: str,
    task_id: str,
    payload: RerunTaskRequest,
    usecase: RerunTaskUseCase = Depends(_get_rerun_task),
) -> Any:
    command = RerunTaskCommand(
        run_id=run_id,
        task_id=task_id,
        new_input=payload.new_input,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get("/{run_id}/context")
async def get_run_context(
    run_id: str,
    usecase: GetRunContextUseCase = Depends(_get_get_run_context),
) -> Any:
    command = GetRunContextCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


@router.get(
    "/{run_id}/nodes/{node_id}/variable-suggestions",
    summary="List reachable variable paths for a node",
    description=(
        "Returns the paths a designer can reference via `{{$node.…}}` or "
        "`{{$artifacts.…}}`. Walks upstream schemas recursively (depth cap "
        "10), auto-expands file-refs, and classifies each path with one of "
        "5 ``kind`` values: scalar / file_ref / artifact / artifact_row / "
        "artifact_column."
    ),
    tags=["variable-suggestions"],
    responses={404: _ERR},
)
async def get_variable_suggestions(
    run_id: str,
    node_id: str,
    environment_id: str = Query("", description="SA-2365: preview this environment's $env vars in the panel"),
    usecase: GetVariableSuggestionsUseCase = Depends(_get_get_variable_suggestions),
) -> Any:
    command = GetVariableSuggestionsCommand(run_id=run_id, node_id=node_id, environment_id=environment_id)
    result = await usecase.execute(command)
    if result.error:
        result.scopes = []
    return dataclasses.asdict(result)


@router.get(
    "/{run_id}/nodes/{node_id}/output",
    summary="A node's produced output, per execution",
    description=(
        "Returns the selected node's output as FE-ready `Scope` / "
        "`VariablePath` records — the output-side analogue of "
        "`variable-suggestions`. One entry per execution in `iterations`: "
        "loop bodies / retries yield several, LOOP nodes expand `results[]`, "
        "PENDING nodes collapse to one. Structure + samples only — no "
        "preview rows."
    ),
    tags=["node-output"],
    responses={404: _ERR},
)
async def get_node_output(
    run_id: str,
    node_id: str,
    usecase: GetNodeOutputUseCase = Depends(_get_get_node_output),
) -> Any:
    command = GetNodeOutputCommand(run_id=run_id, node_id=node_id)
    result = await usecase.execute(command)
    if result.error:
        result.iterations = []
    return dataclasses.asdict(result)


# ── Node schema / data / run artifacts (read-side — Unit C) ─────────

from fastapi import Header, Response
from typing import Annotated

IfNoneMatch = Annotated[str | None, Header(alias="If-None-Match")]


@router.get(
    "/{run_id}/nodes/{node_id}/schema",
    summary="Auto-form schema + version + source for a node",
    description=(
        "Returns the opaque auto-form schema (FE-authored for static nodes; "
        "handler-computed for DATA_EDIT). Supports weak-ETag caching via "
        "`If-None-Match` → 304. `schema_source` distinguishes `design_time` "
        "from `runtime` so FE knows whether to show a 'Refresh schema' "
        "affordance."
    ),
    tags=["node-schema"],
    responses={
        200: _ok(GetNodeSchemaResponse),
        304: {"description": "Not Modified — client's If-None-Match matched."},
        404: _ERR, 409: _ERR,
    },
)
async def get_node_schema(
    run_id: str,
    node_id: str,
    response: Response,
    task_id: str | None = None,
    if_none_match: IfNoneMatch = None,
    usecase: GetNodeSchemaUseCase = Depends(_get_get_node_schema),
) -> Any:
    """Auto-form schema + versioning. Opaque ``schema`` blob; BE never interprets
    it. Supports ``?task_id=`` for loop-iteration targeting and ``If-None-Match``
    for client caching."""
    command = GetNodeSchemaCommand(run_id=run_id, node_id=node_id, task_id=task_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    if _etag_not_modified(if_none_match, result.schema_version):
        return Response(status_code=304)
    response.headers["ETag"] = NodeInputEtag.as_weak_header(result.schema_version)
    response.headers["Cache-Control"] = "private, max-age=30"
    return dataclasses.asdict(result)


@router.get(
    "/{run_id}/nodes/{node_id}/data",
    summary="Scalars + recursive artifact descriptor tree for a node",
    description=(
        "Returns `scalars` (inline values from the main CSV) and a recursive "
        "`root.children[]` descriptor tree of artifact-backed tables. "
        "**Identity only** — no inline rows, no row_count. FE fetches table "
        "rows via the scoped `/artifacts/{artifact_id}/odata` endpoint using "
        "the returned `file_id`s. `side=input|output|both` toggles the "
        "view; default `input`. Supports `If-None-Match` → 304."
    ),
    tags=["node-data"],
    responses={
        200: _ok(GetNodeDataResponse),
        304: {"description": "Not Modified."},
        404: _ERR, 410: _ERR,
    },
)
async def get_node_data(
    run_id: str,
    node_id: str,
    response: Response,
    side: str = "input",
    task_id: str | None = None,
    if_none_match: IfNoneMatch = None,
    usecase: GetNodeDataUseCase = Depends(_get_get_node_data),
) -> Any:
    """Scalars + recursive descriptor tree (identity only — no inline rows,
    no row_count). FE fetches rows via the scoped OData endpoint against the
    returned ``file_id``. Supports ``?side=input|output|both`` and ``?task_id=``."""
    if side not in ("input", "output", "both"):
        return error_response(
            "BAD_REQUEST",
            f"side must be one of 'input', 'output', 'both'; got {side!r}",
            status_code=400,
        )
    command = GetNodeDataCommand(run_id=run_id, node_id=node_id, side=side, task_id=task_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    if _etag_not_modified(if_none_match, result.etag):
        return Response(status_code=304)
    response.headers["ETag"] = NodeInputEtag.as_weak_header(result.etag)
    return dataclasses.asdict(result)


@router.get(
    "/{run_id}/artifacts",
    summary="Run-level $artifacts registry",
    description=(
        "Returns the run's named-artifact registry — `{name: metadata_object}` "
        "covering every artifact registered by completed or resumed nodes. "
        "Each entry carries `file_id`, `filename`, `content_type`, "
        "`size_bytes`, and provenance (`produced_by_node`, `produced_by_task`, "
        "`registered_at`). Supports `If-None-Match` → 304."
    ),
    tags=["run-artifacts"],
    responses={
        200: _ok(GetRunArtifactsResponse),
        304: {"description": "Not Modified."},
        404: _ERR,
    },
)
async def get_run_artifacts(
    run_id: str,
    response: Response,
    if_none_match: IfNoneMatch = None,
    usecase: GetRunArtifactsUseCase = Depends(_get_get_run_artifacts),
) -> Any:
    """Run-level ``$artifacts`` registry: ``{name: metadata_object}`` covering
    every named artifact registered by completed or resumed nodes."""
    command = GetRunArtifactsCommand(run_id=run_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    if _etag_not_modified(if_none_match, result.etag):
        return Response(status_code=304)
    response.headers["ETag"] = NodeInputEtag.as_weak_header(result.etag)
    return dataclasses.asdict(result)


@router.patch(
    "/{run_id}/nodes/{node_id}/data",
    summary="Row-level atomic edit of the node's working-copy data",
    description=(
        "Apply a batch of row-level operations to a single artifact. Body: "
        "`{artifact_id, operations:[{operation, row_id, data}]}` where "
        "`operation` is one of `insert` / `edit` / `delete`. For object-typed "
        "artifacts (root CSV for scalars) `insert` and `edit` both upsert "
        "the single row in place; `delete` is rejected. For array-typed "
        "artifacts all three ops are accepted and forwarded to the "
        "data-factory. Requires `If-Match` (409 if missing, 412 on stale). "
        "On any failure the whole batch rolls back."
    ),
    tags=["node-data"],
    responses={
        200: _ok(PatchNodeDataResponse),
        400: _ERR, 403: _ERR, 404: _ERR, 409: _ERR, 410: _ERR, 412: _ERR,
        413: _ERR, 422: _ERR, 423: _ERR,
    },
)
async def patch_node_data(
    run_id: str,
    node_id: str,
    body: PatchNodeDataRequest,
    response: Response,
    request: Request,
    task_id: str | None = None,
    if_match: IfMatch = None,
    usecase: EditNodeDataUseCase = Depends(_get_edit_node_data),
) -> Any:
    """Row-level atomic edit of a node's working-copy __file_ref tree.

    Object artifacts (root CSV for scalars) accept only ``insert`` and upsert
    a single row. Array artifacts accept ``insert`` / ``edit`` / ``delete``
    and forward to the data-factory row-transform service.

    Phase 3c: the command is enqueued as an ``EditNodeDataAction`` and
    processed by the orchestration worker. Same-run PATCHes serialise
    at the single-consumer worker.
    """
    artifacts_payload = [a.model_dump(mode="json") for a in body.artifacts]
    command = EditNodeDataCommand(
        run_id=run_id,
        node_id=node_id,
        task_id=task_id,
        if_match=if_match,
        artifacts=artifacts_payload,
    )
    container = request.app.state.container
    queue = container.get("_orchestration_queue")
    registry = container.get("_orchestration_response_registry")
    # Mock/test wiring may skip the queue — fall back to direct execution
    # so tests that don't spin up a worker still exercise the use case.
    if queue is None or registry is None:
        result = await usecase.execute(command)
    else:
        correlation_id, future = registry.allocate()
        try:
            await queue.enqueue(EditNodeDataAction(
                run_id=run_id,
                correlation_id=correlation_id,
                command=command,
                origin_instance_id=container.get("_instance_id", ""),
            ))
            result = await asyncio.wait_for(future, timeout=30.0)
        finally:
            registry.release(correlation_id)
    err = _check_error(result)
    if err:
        return err
    response.headers["ETag"] = NodeInputEtag.as_weak_header(result.etag)
    return dataclasses.asdict(result)


# ── POST /nodes/{id}/submit (Unit E) ─────────────────────────────────

@router.post(
    "/{run_id}/nodes/{node_id}/submit",
    summary="Finalize a pending node",
    description=(
        "Transitions a PENDING node (INPUT / HUMAN_ACTION / DATA_EDIT / "
        "FILE_UPLOAD) to COMPLETED and dispatches downstream. Data must "
        "already be in the working copy via prior `PATCH /data` calls — "
        "`submit` has no body. Optional `?task_id=` to target a specific "
        "loop iteration; default is the latest pending task for the node."
    ),
    tags=["node-data"],
    responses={
        200: _ok(SubmitNodeResponse),
        404: _ERR, 409: _ERR, 422: _ERR, 423: _ERR,
    },
)
async def submit_node(
    run_id: str,
    node_id: str,
    task_id: str | None = None,
    usecase: SubmitNodeUseCase = Depends(_get_submit_node),
) -> Any:
    """Finalize a pending node. Data should already have been written via
    ``PATCH /data``; submit is a state transition only (no body)."""
    command = SubmitNodeCommand(run_id=run_id, node_id=node_id, task_id=task_id)
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


# ── POST /runs/{id}/nodes/{node_id}/trigger (Manual Mode) ────────────


class TriggerNodeRequest(BaseModel):
    force: bool = False  # Force trigger even if node already completed/failed


@router.post(
    "/{run_id}/nodes/{node_id}/trigger",
    summary="Trigger a node in manual execution mode",
    description=(
        "Manually triggers execution of a specific node in a workflow run "
        "that was created with `execution_mode: 'manual'`. The run must be "
        "in SUSPENDED status. Use `force: true` to re-trigger a node that "
        "has already been executed."
    ),
    tags=["manual-mode"],
    responses={
        200: {"description": "Node triggered successfully"},
        400: _ERR, 404: _ERR, 409: _ERR,
    },
)
async def trigger_node(
    run_id: str,
    node_id: str,
    body: TriggerNodeRequest | None = None,
    usecase: TriggerNodeUseCase = Depends(_get_trigger_node),
) -> Any:
    """Trigger a node in manual execution mode."""
    b = body or TriggerNodeRequest()
    command = TriggerNodeCommand(
        run_id=run_id,
        node_id=node_id,
        force=b.force,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


# ── GET /nodes/{id}/artifacts/{aid}/odata (Unit E) ───────────────────

@router.get(
    "/{run_id}/nodes/{node_id}/artifacts/{artifact_id}/odata",
    summary="Scoped OData query against a node's artifact",
    description=(
        "Tenancy-enforced OData wrapper: verifies the `artifact_id` belongs "
        "to the node's __file_ref tree, resolves to the current file_id, "
        "and forwards all OData query params (`$filter`, `$orderby`, `$skip`, "
        "`$top`, `$count`, `$select`, `$expand`) to the file service "
        "unmodified. The control-plane does not parse OData."
    ),
    tags=["node-data"],
    responses={
        200: _ok(ScopedODataResponse),
        403: _ERR, 404: _ERR,
    },
)
async def query_node_artifact_odata(
    run_id: str,
    node_id: str,
    artifact_id: str,
    request: Request,
    task_id: str | None = None,
    usecase: QueryNodeArtifactUseCase = Depends(_get_query_node_artifact),
) -> Any:
    """Scoped OData query against a node's artifact. Tenancy-enforced: the
    artifact must exist in the run's node descriptor tree.
    All query params are forwarded to the file service unmodified."""
    # Forward all OData query params except task_id which we consume ourselves.
    qp: dict[str, str] = {}
    for k, v in request.query_params.items():
        if k == "task_id":
            continue
        qp[k] = v
    command = QueryNodeArtifactCommand(
        run_id=run_id,
        node_id=node_id,
        artifact_id=artifact_id,
        task_id=task_id,
        query_params=qp,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    # Return the file-service response directly (already has {data, total, ...}).
    return result.response


# ── POST /runs/{id}/rerun (Unit E) ───────────────────────────────────

@router.post(
    "/{run_id}/rerun",
    summary="Run-level rerun — cascade-reset from a node or from entry nodes",
    description=(
        "With `from_node_id`: cascade-reset every downstream node via "
        "`collect_downstream_nodes_to_reset`, wipe their task states, and "
        "dispatch from the anchor. Without `from_node_id`: rerun from every "
        "entry node (full run restart). Optional `variables` overrides are "
        "merged into `run.context.variables` before dispatch."
    ),
    tags=["run-rerun"],
    responses={
        200: _ok(RerunRunResponse),
        404: _ERR, 500: _ERR,
    },
)
async def rerun_run(
    run_id: str,
    body: RerunRunRequestBody | None = None,
    usecase: RerunRunUseCase = Depends(_get_rerun_run),
) -> Any:
    """Run-level rerun. With ``from_node_id``: cascade-reset from that node
    and dispatch. Without: restart from all entry nodes. ``variables`` merged
    into ``run.context.variables`` before dispatch."""
    b = body or RerunRunRequestBody()
    command = RerunRunCommand(
        run_id=run_id,
        from_node_id=b.from_node_id,
        rerun_strategy=b.rerun_strategy or "cascade",
        variables=b.variables,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


# ── POST /runs/{id}/resolve (Unit E) ─────────────────────────────────

@router.post(
    "/{run_id}/resolve",
    summary="Batched variable-path evaluator for builder preview",
    description=(
        "Evaluates a batch of variable expressions (`{{$node.…}}` or "
        "`{{$artifacts.…}}`) against the run's current context. Per-path "
        "failures surface in `results[i].error` without failing the batch. "
        "Limit: 100 paths per call. Primary consumer is the workflow "
        "builder's 'preview this binding' affordance."
    ),
    tags=["run-resolve"],
    responses={
        200: _ok(ResolvePathsResponse),
        400: _ERR, 404: _ERR, 413: _ERR, 422: _ERR,
    },
)
async def resolve_variable_paths(
    run_id: str,
    body: ResolvePathsRequestBody,
    usecase: ResolveVariablePathsUseCase = Depends(_get_resolve_variable_paths),
) -> Any:
    """Batched variable-path evaluator for the builder preview.
    Evaluates each path independently; per-path failures surface in
    ``results[i].error`` without failing the batch."""
    command = ResolveVariablePathsCommand(
        run_id=run_id,
        paths=list(body.paths),
        task_id=body.task_id,
    )
    result = await usecase.execute(command)
    err = _check_error(result)
    if err:
        return err
    return dataclasses.asdict(result)


# ── Replay Debugger (SA-1372) ─────────────────────────────────────────
# Non-mutating, deterministic time-travel over a run's recorded event log.
# Replay is a read-only SECOND consumer of the append-only log under a cursor —
# distinct from ``/rerun`` (which resets + re-dispatches). See the
# ``replay_debugger`` feature (auto-discovered into the container).

def _get_replay_debugger(request: Request) -> ReplayDebuggerUseCase:
    return request.app.state.container["replay_debugger"]


class ReplayStepRequest(BaseModel):
    op: str = "step"                 # "step" | "run_to" | "index" | "pause"
    n: int = 1                       # step delta when op="step" (negative steps back)
    node_id: str | None = None       # target node when op="run_to"
    index: int | None = None         # absolute cursor when op="index"


@router.post(
    "/{run_id}/replay",
    summary="Open a replay-debugger session over a run's recorded event log",
    tags=["run-replay"],
)
async def open_replay(
    run_id: str,
    timeline_limit: int = 100,
    usecase: ReplayDebuggerUseCase = Depends(_get_replay_debugger),
) -> Any:
    """Open a non-mutating replay session. Returns the session id, the total
    step count, and the FIRST PAGE of the recorded-event timeline (ordered by
    per-run sequence) — further pages via GET .../replay/{session_id}/timeline."""
    return result_or_error(usecase.open(run_id, timeline_limit=min(max(0, timeline_limit), 1000)))


@router.get(
    "/{run_id}/replay/{session_id}/timeline",
    summary="One page of the replay timeline (limit/offset, ordered by sequence)",
    tags=["run-replay"],
)
async def get_replay_timeline(
    run_id: str,
    session_id: str,
    offset: int = 0,
    limit: int = 100,
    usecase: ReplayDebuggerUseCase = Depends(_get_replay_debugger),
) -> Any:
    """Paged timeline for an open replay session — same ``limit``/``offset`` +
    ``total`` convention as ``GET /runs/{id}/execution-log``. Entry ``index``
    numbering continues across pages (``offset + i + 1``)."""
    return result_or_error(
        usecase.timeline(session_id, offset=max(0, offset), limit=min(max(0, limit), 1000))
    )


@router.post(
    "/{run_id}/replay/{session_id}/step",
    summary="Advance / rewind a replay cursor and inspect state at that point",
    tags=["run-replay"],
)
async def step_replay(
    run_id: str,
    session_id: str,
    body: ReplayStepRequest | None = None,
    usecase: ReplayDebuggerUseCase = Depends(_get_replay_debugger),
) -> Any:
    """Move the cursor (step / run_to / index / pause) and return the folded
    state at the cursor — scope, node I/O, taken edges — secret-redacted."""
    b = body or ReplayStepRequest()
    return result_or_error(
        usecase.step(session_id, op=b.op, n=b.n, node_id=b.node_id, index=b.index)
    )


@router.get(
    "/{run_id}/replay/{session_id}/state",
    summary="Read the folded, secret-redacted state at the current replay cursor",
    tags=["run-replay"],
)
async def get_replay_state(
    run_id: str,
    session_id: str,
    usecase: ReplayDebuggerUseCase = Depends(_get_replay_debugger),
) -> Any:
    return result_or_error(usecase.state(session_id))


@router.delete(
    "/{run_id}/replay/{session_id}",
    summary="Close a replay session (the source log is untouched)",
    tags=["run-replay"],
)
async def close_replay(
    run_id: str,
    session_id: str,
    usecase: ReplayDebuggerUseCase = Depends(_get_replay_debugger),
) -> Any:
    return result_or_error(usecase.close(session_id))
