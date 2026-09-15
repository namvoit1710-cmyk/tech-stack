import dataclasses

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, field_validator
from typing import Any

from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseOutputDto
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response, result_or_error
from app.layer2_application.features.create_workflow.use_cases.create_workflow_use_case import CreateWorkflowUseCase, CreateWorkflowCommand
from app.layer2_application.features.list_workflows.use_cases.list_workflows_use_case import ListWorkflowsUseCase, ListWorkflowsCommand
from app.layer2_application.features.get_workflow.use_cases.get_workflow_use_case import GetWorkflowUseCase, GetWorkflowCommand, GetWorkflowByRoutingPathCommand
from app.layer2_application.features.update_workflow.use_cases.update_workflow_use_case import (
    UpdateWorkflowUseCase,
    ReplaceWorkflowCommand,
    PatchWorkflowCommand,
)
from app.layer2_application.features.delete_workflow.use_cases.delete_workflow_use_case import DeleteWorkflowUseCase, DeleteWorkflowCommand
from app.layer2_application.features.dry_run_workflow.use_cases.dry_run_workflow_use_case import DryRunWorkflowUseCase, DryRunWorkflowCommand
from app.layer2_application.features.test_node.use_cases.test_node_use_case import TestNodeUseCase, TestNodeCommand
from app.layer2_application.features.reset_test_run.use_cases.reset_test_run_use_case import ResetTestRunUseCase, ResetTestRunCommand
from app.layer2_application.features.test_full_workflow.use_cases.test_full_workflow_use_case import TestFullWorkflowUseCase, TestFullWorkflowCommand
from app.layer2_application.features.set_main_flow.use_cases.set_main_flow_use_case import SetMainFlowUseCase, SetMainFlowCommand
from app.layer2_application.features.list_main_flows.use_cases.list_main_flows_use_case import ListMainFlowsUseCase, ListMainFlowsCommand
from app.layer2_application.features.get_workflow_node_tree.use_cases.get_workflow_node_tree_use_case import GetWorkflowNodeTreeUseCase, GetWorkflowNodeTreeCommand
from app.layer2_application.features.publish_workflow.use_cases.publish_workflow_use_case import PublishWorkflowUseCase, PublishWorkflowCommand
from app.layer2_application.features.get_worker_config.use_cases.get_worker_config_use_case import GetWorkerConfigUseCase, GetWorkerConfigCommand
from app.layer2_application.features.set_worker_config.use_cases.set_worker_config_use_case import SetWorkerConfigUseCase, SetWorkerConfigCommand
from app.layer2_application.features.get_design_time_variable_suggestions.use_cases.get_design_time_variable_suggestions_use_case import (
    GetDesignTimeVariableSuggestionsUseCase,
    GetDesignTimeVariableSuggestionsCommand,
)
from app.layer2_application.features.set_workflow_owner.use_cases.set_workflow_owner_use_case import (
    SetWorkflowOwnerUseCase,
    SetWorkflowOwnerCommand,
)
from app.layer3_adapters.controllers.restful.v1.auth_dependencies import resolve_actor


# ── Request DTOs ──────────────────────────────────────────────────


class _SchemaCoercionMixin:
    @field_validator("input_schema", "output_schema", mode="before")
    @classmethod
    def _coerce_schema(cls, v: Any) -> Any:
        if isinstance(v, dict):
            if not v:
                return []
            if "fields" in v and isinstance(v["fields"], list):
                return v["fields"]
            return []
        return v


class CreateWorkflowRequest(_SchemaCoercionMixin, BaseModel):
    name: str
    description: str = ""
    input_schema: list[dict[str, Any]] = []
    output_schema: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    schema_version: str = "1.0.0"
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = []


class ReplaceWorkflowRequest(_SchemaCoercionMixin, BaseModel):
    """Full replacement (PUT) — name is required, all other fields have defaults."""
    name: str
    version: str
    description: str = ""
    input_schema: list[dict[str, Any]] = []
    output_schema: list[dict[str, Any]] = []
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    schema_version: str = "1.0.0"
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = []
    is_confirm: bool = False


class PatchWorkflowRequest(_SchemaCoercionMixin, BaseModel):
    """Partial update (PATCH) — only provided fields are applied."""
    name: str | None = None
    description: str | None = None
    input_schema: list[dict[str, Any]] | None = None
    output_schema: list[dict[str, Any]] | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    schema_version: str | None = None
    routing_path: str | None = None
    main_flow: bool | None = None
    action: str | None = None
    instruction: str | None = None
    agents: list[dict[str, Any]] | None = None


class SetMainFlowRequest(BaseModel):
    main_flow: bool


# ── Response DTOs ─────────────────────────────────────────────────


class WorkflowOutputDto(_SchemaCoercionMixin, BaseOutputDto):
    id: str
    name: str
    version: str
    description: str
    status: str
    nodes: list[dict] = []
    edges: list[dict] = []
    input_schema: list[dict] = []
    output_schema: list[dict] = []
    metadata: dict = {}
    created_at: str = ""
    schema_version: str = "1.0.0"
    test_run_id: str | None = None
    routing_path: str | None = None
    main_flow: bool = False
    all_input_schema: list[list[dict]] = []
    action: str = ""
    instruction: str = ""
    agents: list[dict] = []
    # SA-2570: workflow attribution.
    created_by: str = ""
    updated_by: str = ""
    owner: str | None = None


# ── Dependency helpers ────────────────────────────────────────────


def _get_create_workflow(request: Request) -> CreateWorkflowUseCase:
    return request.app.state.container["create_workflow"]


def _get_list_workflows(request: Request) -> ListWorkflowsUseCase:
    return request.app.state.container["list_workflows"]


def _get_get_workflow(request: Request) -> GetWorkflowUseCase:
    return request.app.state.container["get_workflow"]


def _get_update_workflow(request: Request) -> UpdateWorkflowUseCase:
    return request.app.state.container["update_workflow"]


def _get_delete_workflow(request: Request) -> DeleteWorkflowUseCase:
    return request.app.state.container["delete_workflow"]



def _get_dry_run_workflow(request: Request) -> DryRunWorkflowUseCase:
    return request.app.state.container["dry_run_workflow"]


def _get_test_node(request: Request) -> TestNodeUseCase:
    return request.app.state.container["test_node"]


def _get_reset_test_run(request: Request) -> ResetTestRunUseCase:
    return request.app.state.container["reset_test_run"]


def _get_test_full_workflow(request: Request) -> TestFullWorkflowUseCase:
    return request.app.state.container["test_full_workflow"]


def _get_get_worker_config(request: Request) -> GetWorkerConfigUseCase:
    return request.app.state.container["get_worker_config"]


def _get_set_worker_config(request: Request) -> SetWorkerConfigUseCase:
    return request.app.state.container["set_worker_config"]


def _get_set_main_flow(request: Request) -> SetMainFlowUseCase:
    return request.app.state.container["set_main_flow"]


def _get_list_main_flows(request: Request) -> ListMainFlowsUseCase:
    return request.app.state.container["list_main_flows"]


def _get_get_workflow_node_tree(request: Request) -> GetWorkflowNodeTreeUseCase:
    return request.app.state.container["get_workflow_node_tree"]

def _get_get_latest_publish_workflow(request: Request) -> GetWorkflowUseCase:
    return request.app.state.container["get_workflow"]

def _get_publish_workflow(request: Request) -> PublishWorkflowUseCase:
    return request.app.state.container["publish_workflow"]

def _get_design_time_variable_suggestions(request: Request) -> GetDesignTimeVariableSuggestionsUseCase:
    return request.app.state.container["get_design_time_variable_suggestions"]


def _get_set_workflow_owner(request: Request) -> SetWorkflowOwnerUseCase:
    return request.app.state.container["set_workflow_owner"]


class PublishWorkflowRequest(BaseModel):
    version: str = ""
    is_confirm: bool = False


class DryRunWorkflowRequest(BaseModel):
    input_data: dict[str, Any] | None = None


class TestNodeRequest(BaseModel):
    input_data: dict[str, Any] = {}
    mock_variables: dict[str, Any] = {}
    # Tenant + caller identity for vault credential resolution. Default "system"
    # matches tenant-1's FE headers so a credential-using node authenticates the
    # same as a real run; the FE MAY override for other tenants (StartRun parity).
    tenant_id: str = "system"
    user_id: str = "system"


class TestFullWorkflowRequest(BaseModel):
    input_data: dict[str, Any] = {}
    workflow_version: str = ""
    skip_validation: bool = True
    # Tenant + caller identity for vault credential resolution — see
    # TestNodeRequest. Default "system" makes tenant-1 resolve the node's owned
    # credential; overridable by the FE for other tenants (StartRun parity).
    tenant_id: str = "system"
    user_id: str = "system"
    # SA-2365: environment selected for this test run; its non-secret variables
    # are injected as the $env source (parity with StartRun).
    environment_id: str = ""
    # SA-2424: optional per-node worker-instance pin (debug routing).
    # {"mode": "WHOLE_FLOW"|"PER_NODE", "by_worker_type": {...}, "by_node": {...}}.
    # Omit / {} = no pin (normal freshest-healthy selection).
    instance_selection: dict[str, Any] = {}


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.post("")
async def create_workflow(
    payload: CreateWorkflowRequest,
    request: Request,
    usecase: CreateWorkflowUseCase = Depends(_get_create_workflow),
) -> Any:
    # SA-2570: attribute the workflow to the verified caller (not the body). The
    # creator + default owner are derived server-side.
    actor = resolve_actor(request).get("id") or "system"
    command = CreateWorkflowCommand(
        **payload.model_dump(), created_by=actor, owner=actor, updated_by=actor,
    )
    result = await usecase.execute(command)
    if hasattr(result, "error") and result.error:
        # result_or_error propagates error_code + error_details — the
        # NODE_CONFIG_INVALID contract carries per-field violations the
        # FE renders; the old flat BAD_REQUEST dropped both.
        return result_or_error(result)
    return WorkflowOutputDto.from_dataclass(result)


@router.get("")
async def list_workflows(
    request: Request,
    usecase: ListWorkflowsUseCase = Depends(_get_list_workflows),
) -> Any:
    from app.layer3_adapters.controllers.restful.v1.dtos.odata_params import (
        parse_odata_params, parse_odata_filter, odata_page_response,
    )
    params = parse_odata_params(request)
    filters = parse_odata_filter(params.filter)
    main_flow_raw = filters.eq.get("main_flow")
    main_flow = bool(main_flow_raw) if main_flow_raw is not None else None
    # Opt-in: ?prefer_published=true resolves each workflow to its PUBLISHED version
    # when one exists (else latest-edited). Off by default — the editor list stays
    # latest-edited (SA-1491); a "published/runtime" view passes this flag.
    prefer_published = request.query_params.get("prefer_published", "").lower() in ("true", "1")
    command = ListWorkflowsCommand(
        tenant_id=filters.eq.get("tenant_id"),
        main_flow=main_flow,
        prefer_published=prefer_published,
        skip=params.skip,
        top=params.top,
        orderby=params.orderby,
        eq=filters.eq,
        ne=filters.ne,
        gt=filters.gt,
        ge=filters.ge,
        lt=filters.lt,
        le=filters.le,
        contains=filters.contains,
        id=params.id,
        version=params.version,
        status=params.status,
    )
    result = await usecase.execute(command)
    return odata_page_response(result.items, result.total, params)


@router.get("/main")
async def list_main_flows(usecase: ListMainFlowsUseCase = Depends(_get_list_main_flows)) -> Any:
    result = await usecase.execute(ListMainFlowsCommand())
    return dataclasses.asdict(result)


@router.get("/by-path/{routing_path:path}")
async def get_workflow_by_routing_path(
    routing_path: str,
    usecase: GetWorkflowUseCase = Depends(_get_get_workflow),
) -> Any:
    # Ensure leading slash
    if not routing_path.startswith("/"):
        routing_path = f"/{routing_path}"
    command = GetWorkflowByRoutingPathCommand(routing_path=routing_path)
    result = await usecase.get_by_routing_path(command)
    if hasattr(result, "error") and result.error:
        return error_response("NOT_FOUND", result.error, 404)
    return WorkflowOutputDto.from_dataclass(result)


@router.get("/{workflow_id}/node-tree")
async def get_workflow_node_tree(
    workflow_id: str,
    max_depth: int = 10,
    include_edges: bool = False,
    usecase: GetWorkflowNodeTreeUseCase = Depends(_get_get_workflow_node_tree),
) -> Any:
    command = GetWorkflowNodeTreeCommand(
        workflow_id=workflow_id,
        max_depth=max_depth,
        include_edges=include_edges,
    )
    result = await usecase.execute(command)
    if result.error:
        return error_response("NOT_FOUND", result.error, 404)
    return dataclasses.asdict(result)


@router.get("/{workflow_id}")
async def get_workflow(
    workflow_id: str,
    usecase: GetWorkflowUseCase = Depends(_get_get_workflow),
) -> Any:
    command = GetWorkflowCommand(workflow_id=workflow_id)
    result = await usecase.execute(command)
    if hasattr(result, "error") and result.error:
        return error_response("NOT_FOUND", result.error, 404)
    return WorkflowOutputDto.from_dataclass(result)

@router.get("/{workflow_id}/version/{version}")
async def get_workflow_by_version(
    workflow_id: str,
    version: str,
    usecase: GetWorkflowUseCase = Depends(_get_get_workflow),
):
    command = GetWorkflowCommand(workflow_id=workflow_id, version=version)
    result = await usecase.execute(command)
    if hasattr(result, "error") and result.error:
        return error_response("NOT_FOUND", result.error, 404)
    return WorkflowOutputDto.from_dataclass(result)

@router.get("/publish/{workflow_id}")
async def get_latest_publish_workflow(
    workflow_id: str,
    usecase: GetWorkflowUseCase = Depends(_get_get_latest_publish_workflow),
) -> Any:
    command = GetWorkflowCommand(workflow_id=workflow_id)
    result = await usecase.execute(command, is_publish = True)
    if hasattr(result, "error") and result.error:
        return error_response("NOT_FOUND", result.error, 404)
    return WorkflowOutputDto.from_dataclass(result)

@router.post("/publish/{workflow_id}")
async def publish_workflow(
    workflow_id: str,
    payload: PublishWorkflowRequest,
    usecase: PublishWorkflowUseCase = Depends(_get_publish_workflow),
) -> Any:
    command = PublishWorkflowCommand(workflow_id=workflow_id, version=payload.version, is_confirm=payload.is_confirm)
    result = await usecase.execute(command)
    if hasattr(result, "error") and result.error:
        return result_or_error(result)
    return WorkflowOutputDto.from_dataclass(result)

@router.put("/{workflow_id}")
async def replace_workflow(
    workflow_id: str,
    payload: ReplaceWorkflowRequest,
    request: Request,
    usecase: UpdateWorkflowUseCase = Depends(_get_update_workflow),
) -> Any:
    command = ReplaceWorkflowCommand(
        workflow_id=workflow_id,
        **payload.model_dump(),
        updated_by=resolve_actor(request).get("id") or "",  # SA-2570
    )
    result = await usecase.execute(command)
    if hasattr(result, "error") and result.error:
        return result_or_error(result)
    return WorkflowOutputDto.from_dataclass(result)


@router.patch("/{workflow_id}")
async def patch_workflow(
    workflow_id: str,
    payload: PatchWorkflowRequest,
    request: Request,
    usecase: UpdateWorkflowUseCase = Depends(_get_update_workflow),
) -> Any:
    command = PatchWorkflowCommand(
        workflow_id=workflow_id,
        **payload.model_dump(exclude_none=True),
        updated_by=resolve_actor(request).get("id") or "",  # SA-2570
    )
    result = await usecase.execute(command)
    if hasattr(result, "error") and result.error:
        return result_or_error(result)
    return WorkflowOutputDto.from_dataclass(result)


class SetOwnerRequest(BaseModel):
    owner: str  # the new owner's id (Keycloak ``sub``), from the picker


@router.put("/{workflow_id}/owner")
async def set_workflow_owner(
    workflow_id: str,
    payload: SetOwnerRequest,
    request: Request,
    usecase: SetWorkflowOwnerUseCase = Depends(_get_set_workflow_owner),
) -> Any:
    # SA-2573: reassign the owner. Any authenticated caller may do this — access
    # control is deferred to Keycloak. The verified caller is recorded as
    # updated_by; the body cannot spoof it.
    actor = resolve_actor(request)
    result = await usecase.execute(SetWorkflowOwnerCommand(
        workflow_id=workflow_id,
        owner=payload.owner,
        actor_id=actor.get("id") or "system",
    ))
    if result.error:
        code = result.error_code or "BAD_REQUEST"
        status = 404 if code == "NOT_FOUND" else 403 if code == "FORBIDDEN" else 400
        return error_response(code, result.error, status)
    return dataclasses.asdict(result)


@router.patch("/{workflow_id}/main-flow")
async def set_main_flow(
    workflow_id: str,
    payload: SetMainFlowRequest,
    usecase: SetMainFlowUseCase = Depends(_get_set_main_flow),
) -> Any:
    command = SetMainFlowCommand(workflow_id=workflow_id, main_flow=payload.main_flow)
    result = await usecase.execute(command)
    if result.error:
        return error_response("NOT_FOUND", result.error, 404)
    return dataclasses.asdict(result)


@router.delete("/{workflow_id}")
async def delete_workflow(
    workflow_id: str,
    usecase: DeleteWorkflowUseCase = Depends(_get_delete_workflow),
) -> Any:
    command = DeleteWorkflowCommand(workflow_id=workflow_id)
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("NOT_FOUND", data["error"], 404)
    return data


@router.post("/{workflow_id}/dry-run")
async def dry_run_workflow(
    workflow_id: str,
    payload: DryRunWorkflowRequest,
    usecase: DryRunWorkflowUseCase = Depends(_get_dry_run_workflow),
) -> Any:
    command = DryRunWorkflowCommand(workflow_id=workflow_id, input_data=payload.input_data)
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("BAD_REQUEST", data["error"])
    return data


@router.post("/{workflow_id}/nodes/{node_id}/test")
async def test_node(
    workflow_id: str,
    node_id: str,
    payload: TestNodeRequest,
    usecase: TestNodeUseCase = Depends(_get_test_node),
) -> Any:
    command = TestNodeCommand(
        workflow_id=workflow_id,
        node_id=node_id,
        input_data=payload.input_data,
        mock_variables=payload.mock_variables,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
    )
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("BAD_REQUEST", data["error"])
    return data


@router.get(
    "/{workflow_id}/nodes/{node_id}/variable-suggestions",
    summary="Design-time autocomplete for {{$...}} expressions",
    description=(
        "Schema-derived suggestions for the expression editor; no run "
        "required. `q` is the partial expression as typed (`{{$nA.da`, "
        "`$nA.da`, `da`, or empty = everything visible to the node). "
        "Returns a flat ranked `matches[]` list plus per-scope summary "
        "counters. Runtime-only scopes ($loop / $parent / $root, child-run "
        "nodes, artifact registry entries) and sample values are excluded "
        "at design time — use the run-scoped variable-suggestions endpoint "
        "for those."
    ),
    tags=["variable-suggestions"],
)
async def get_design_time_variable_suggestions(
    workflow_id: str,
    node_id: str,
    q: str = Query("", description="Partial expression as typed"),
    limit: int = Query(50, ge=1, le=200),
    environment_id: str = Query("", description="SA-2365: environment whose $env vars to offer; empty = none"),
    usecase: GetDesignTimeVariableSuggestionsUseCase = Depends(_get_design_time_variable_suggestions),
) -> Any:
    command = GetDesignTimeVariableSuggestionsCommand(
        workflow_id=workflow_id, node_id=node_id, q=q, limit=limit,
        environment_id=environment_id,
    )
    result = await usecase.execute(command)
    return result_or_error(result)


@router.post("/{workflow_id}/test/full")
async def test_full_workflow(
    request: Request,
    workflow_id: str,
    payload: TestFullWorkflowRequest,
    usecase: TestFullWorkflowUseCase = Depends(_get_test_full_workflow),
) -> Any:
    # SA-2558: the run's attribution comes from the server-verified caller, not
    # the request body — a Bearer/M2M caller cannot spoof it. ``user_id`` is left
    # untouched (credential resolution semantics), and a dedicated
    # ``triggered_by`` label is derived from the verified actor for the run's
    # Execution-History chip.
    from app.layer3_adapters.controllers.restful.v1.auth_dependencies import actor_label
    command = TestFullWorkflowCommand(
        workflow_id=workflow_id,
        workflow_version=payload.workflow_version,
        input_data=payload.input_data,
        skip_validation=payload.skip_validation,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        environment_id=payload.environment_id,
        instance_selection=payload.instance_selection,
        triggered_by=actor_label(resolve_actor(request)),
    )
    result = await usecase.execute(command)
    # result_or_error propagates error_code + error_details so a mapped code
    # (e.g. SA-2450 ENVIRONMENT_NOT_ASSIGNED → 422, WORKFLOW_NOT_FOUND → 404)
    # surfaces with the right HTTP status instead of a blanket 400.
    return result_or_error(result)


@router.post("/{workflow_id}/test/reset")
async def reset_test_run(
    workflow_id: str,
    usecase: ResetTestRunUseCase = Depends(_get_reset_test_run),
) -> Any:
    command = ResetTestRunCommand(workflow_id=workflow_id)
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("NOT_FOUND", data["error"], 404)
    return data


# ── SA-2437: per-workflow worker-instance config (debug routing) ──────────────

class SetWorkerConfigRequest(BaseModel):
    mode: str = "WHOLE_FLOW"  # WHOLE_FLOW | PER_NODE
    # { by_worker_type: {wt: {instance_id, host}}, by_node: {node_id: {instance_id, host}} }
    selection: dict[str, Any] = {}
    tenant_id: str = "system"
    updated_by: str = "system"


@router.get("/{workflow_id}/worker-config")
async def get_worker_config(
    workflow_id: str,
    usecase: GetWorkerConfigUseCase = Depends(_get_get_worker_config),
) -> Any:
    cfg = await usecase.execute(GetWorkerConfigCommand(workflow_id=workflow_id))
    if cfg is None:
        # Empty (never-configured) is a valid answer, not 404.
        return {
            "workflow_id": workflow_id, "mode": "WHOLE_FLOW",
            "selection": {"by_worker_type": {}, "by_node": {}},
            "updated_at": "", "updated_by": "",
        }
    return dataclasses.asdict(cfg)


@router.put("/{workflow_id}/worker-config")
async def set_worker_config(
    workflow_id: str,
    payload: SetWorkerConfigRequest,
    usecase: SetWorkerConfigUseCase = Depends(_get_set_worker_config),
) -> Any:
    result = await usecase.execute(SetWorkerConfigCommand(
        workflow_id=workflow_id,
        mode=payload.mode,
        selection=payload.selection,
        tenant_id=payload.tenant_id,
        updated_by=payload.updated_by,
    ))
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("BAD_REQUEST", data["error"])
    return data
