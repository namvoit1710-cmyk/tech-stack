"""REST endpoints for Node Definition CRUD."""

import dataclasses

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Any

from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response, result_or_error
from app.layer3_adapters.controllers.restful.v1.dtos.odata_params import parse_odata_params, parse_odata_filter, odata_page_response
from app.layer2_application.features.create_node_definition.use_cases.create_node_definition_use_case import CreateNodeDefinitionUseCase, CreateNodeDefinitionCommand
from app.layer2_application.features.get_node_definition.use_cases.get_node_definition_use_case import GetNodeDefinitionUseCase, GetNodeDefinitionCommand
from app.layer2_application.features.list_node_definitions.use_cases.list_node_definitions_use_case import ListNodeDefinitionsUseCase, ListNodeDefinitionsCommand
from app.layer2_application.features.update_node_definition.use_cases.update_node_definition_use_case import UpdateNodeDefinitionUseCase, UpdateNodeDefinitionCommand
from app.layer2_application.features.delete_node_definition.use_cases.delete_node_definition_use_case import DeleteNodeDefinitionUseCase, DeleteNodeDefinitionCommand
from app.layer2_application.features.test_node_definition.use_cases.test_node_definition_use_case import TestNodeDefinitionUseCase, TestNodeDefinitionCommand
from app.layer2_application.features.backfill_node_taxonomy.use_cases.backfill_node_taxonomy_use_case import BackfillNodeTaxonomyUseCase


# ── Request DTOs ──────────────────────────────────────────────────


class CreateNodeDefinitionRequest(BaseModel):
    tenant_id: str
    name: str
    description: str = ""
    parent_definition_id: str | None = None
    base_type: str = "TASK"
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] = {}
    input_schema: list[dict[str, Any]] = []
    output_schema: list[dict[str, Any]] = []
    input_mapping: dict[str, Any] = {}
    output_mapping: dict[str, Any] = {}
    icon: str = ""
    color: str = ""
    tags: list[str] = []
    ports: dict[str, Any] = {}
    data_mode: str = "json"
    edit_mode: str = "none"
    # Empty defaults so the entity's __post_init__ smart-default heal (default_kind /
    # collection="My Nodes") stays the single source of truth — a request default here
    # would short-circuit the GET->read / POST->action inference. Explicit client values pass through.
    kind: str = ""
    collection: str = ""


class TestNodeDefinitionRequest(BaseModel):
    input_data: dict[str, Any] = {}


class PatchNodeDefinitionRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    parent_definition_id: str | None = None
    base_type: str | None = None
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] | None = None
    input_schema: list[dict[str, Any]] | None = None
    output_schema: list[dict[str, Any]] | None = None
    input_mapping: dict[str, Any] | None = None
    output_mapping: dict[str, Any] | None = None
    icon: str | None = None
    color: str | None = None
    tags: list[str] | None = None
    ports: dict[str, Any] | None = None
    data_mode: str | None = None
    edit_mode: str | None = None
    kind: str | None = None
    collection: str | None = None


# ── Dependency helpers ────────────────────────────────────────────


def _get_create(request: Request) -> CreateNodeDefinitionUseCase:
    return request.app.state.container["create_node_definition"]


def _get_get(request: Request) -> GetNodeDefinitionUseCase:
    return request.app.state.container["get_node_definition"]


def _get_list(request: Request) -> ListNodeDefinitionsUseCase:
    return request.app.state.container["list_node_definitions"]


def _get_update(request: Request) -> UpdateNodeDefinitionUseCase:
    return request.app.state.container["update_node_definition"]


def _get_delete(request: Request) -> DeleteNodeDefinitionUseCase:
    return request.app.state.container["delete_node_definition"]


def _get_test(request: Request) -> TestNodeDefinitionUseCase:
    return request.app.state.container["test_node_definition"]


def _get_backfill(request: Request) -> BackfillNodeTaxonomyUseCase:
    return request.app.state.container["backfill_node_taxonomy"]


# ── Router ────────────────────────────────────────────────────────

router = APIRouter()


@router.post("")
async def create_node_definition(
    payload: CreateNodeDefinitionRequest,
    usecase: CreateNodeDefinitionUseCase = Depends(_get_create),
) -> Any:
    command = CreateNodeDefinitionCommand(**payload.model_dump())
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("VALIDATION_ERROR", data["error"])
    return data


@router.post("/backfill-taxonomy")
async def backfill_taxonomy(
    tenant_id: str,
    usecase: BackfillNodeTaxonomyUseCase = Depends(_get_backfill),
) -> Any:
    """Ops one-shot: persist (kind, collection) onto ``tenant_id``'s legacy rows
    (SA-1740). ``tenant_id`` is required (FIX4) — this bounds the blast radius
    of a single call to one tenant. NOTE: like every route in this service,
    this endpoint has no auth dependency; tenant-scoping is not the same thing
    as access control, and closing that gap is a service-wide concern outside
    this endpoint's reach.
    """
    result = await usecase.execute(tenant_id=tenant_id)
    return {"status": "ok", "data": dataclasses.asdict(result)}


@router.get("")
async def list_node_definitions(
    request: Request,
    tenant_id: str | None = None,
    usecase: ListNodeDefinitionsUseCase = Depends(_get_list),
) -> Any:
    odata = parse_odata_params(request)
    filters = parse_odata_filter(odata.filter)

    # tenant_id from direct param takes priority; fall back to $filter.
    # filters is an ODataFilterResult (dataclass) — eq terms live in .eq, and it
    # has no .get(); filters.get(...) AttributeErrors → 500 when tenant_id is omitted.
    resolved_tenant = tenant_id or filters.eq.get("tenant_id") or "system"

    command = ListNodeDefinitionsCommand(
        tenant_id=resolved_tenant,
        skip=odata.skip,
        top=odata.top,
        orderby=odata.orderby,
    )
    result = await usecase.execute(command)
    return odata_page_response(result.items, result.total, odata)


@router.get("/{node_definition_id}")
async def get_node_definition(
    node_definition_id: str,
    usecase: GetNodeDefinitionUseCase = Depends(_get_get),
) -> Any:
    command = GetNodeDefinitionCommand(node_definition_id=node_definition_id)
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("NOT_FOUND", data["error"], 404)
    return data


@router.patch("/{node_definition_id}")
async def update_node_definition(
    node_definition_id: str,
    payload: PatchNodeDefinitionRequest,
    usecase: UpdateNodeDefinitionUseCase = Depends(_get_update),
) -> Any:
    command = UpdateNodeDefinitionCommand(
        node_definition_id=node_definition_id,
        **payload.model_dump(exclude_none=True),
    )
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("VALIDATION_ERROR", data["error"])
    return data


@router.delete("/{node_definition_id}")
async def delete_node_definition(
    node_definition_id: str,
    usecase: DeleteNodeDefinitionUseCase = Depends(_get_delete),
) -> Any:
    command = DeleteNodeDefinitionCommand(node_definition_id=node_definition_id)
    result = await usecase.execute(command)
    data = dataclasses.asdict(result)
    if data.get("error"):
        return error_response("NOT_FOUND", data["error"], 404)
    return data


@router.post("/{node_definition_id}/test")
async def test_node_definition(
    node_definition_id: str,
    payload: TestNodeDefinitionRequest,
    usecase: TestNodeDefinitionUseCase = Depends(_get_test),
) -> Any:
    command = TestNodeDefinitionCommand(
        node_definition_id=node_definition_id,
        input_data=payload.input_data,
    )
    result = await usecase.execute(command)
    # Request-level errors (not found / not testable / service / validation)
    # carry an error_code → map to the proper HTTP status. A worker/handler
    # *outcome* (COMPLETED, or FAILED without an error_code) is a successful
    # test → return it as 200 with the outcome in the body.
    if result.error_code:
        return result_or_error(result)
    data = dataclasses.asdict(result)
    for key in ("error_code", "error_details"):
        data.pop(key, None)
    return data
