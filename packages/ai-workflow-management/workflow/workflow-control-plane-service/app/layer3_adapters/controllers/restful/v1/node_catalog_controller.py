"""REST endpoints for /api/v2/node-catalog (Phase 1).

Three endpoints:

- ``GET  /node-catalog``                  — list summaries for a tenant
- ``POST /node-catalog/details``          — batch fetch (up to 50 refs)
- ``GET  /node-catalog/{ref:path}``       — single detail

ETag + 304 handling is delegated to ``ETagMiddleware``, scoped to the
``/api/v2/node-catalog`` prefix.

All endpoints are gated on ``settings.ENABLE_NODE_CATALOG_V2``. When the
flag is off, the router is not mounted and requests 404.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.layer1_domain.value_objects.node_ref import parse_node_ref
from app.layer2_application.features.get_node_catalog_detail.use_cases.get_node_catalog_detail_use_case import (
    GetNodeCatalogDetailCommand,
    GetNodeCatalogDetailUseCase,
)
from app.layer2_application.features.get_node_catalog_details_batch.use_cases.get_node_catalog_details_batch_use_case import (
    BatchSizeExceededError,
    GetNodeCatalogDetailsBatchCommand,
    GetNodeCatalogDetailsBatchUseCase,
    NodeRef,
)
from app.layer2_application.features.list_node_catalog.use_cases.list_node_catalog_use_case import (
    ListNodeCatalogCommand,
    ListNodeCatalogUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.node_catalog_dtos import BatchRefsDto
from app.layer3_adapters.controllers.restful.v1.tenant_gating import tenant_is_gated_out
from app.layer4_frameworks.config.app_config import settings
from typing import Any


router = APIRouter(tags=["node-catalog"])


def _get_list_uc(request: Request) -> ListNodeCatalogUseCase:
    return request.app.state.container["list_node_catalog"]


def _get_detail_uc(request: Request) -> GetNodeCatalogDetailUseCase:
    return request.app.state.container["get_node_catalog_detail"]


def _get_batch_uc(request: Request) -> GetNodeCatalogDetailsBatchUseCase:
    return request.app.state.container["get_node_catalog_details_batch"]


def _gated_out(tenant_id: str) -> JSONResponse | None:
    """Return a 404 JSONResponse when ``tenant_id`` isn't on the allowlist,
    else None (caller continues with normal processing)."""
    if tenant_is_gated_out(tenant_id, settings.NODE_CATALOG_V2_TENANT_ALLOWLIST):
        return JSONResponse(
            status_code=404,
            content={"status": "error", "error": {"code": "NOT_FOUND", "message": "not found"}},
        )
    return None


@router.get("/node-catalog")
async def list_node_catalog(
    tenant_id: str = "system",
    usecase: ListNodeCatalogUseCase = Depends(_get_list_uc),
) -> Any:
    gated = _gated_out(tenant_id)
    if gated is not None:
        return gated
    result = await usecase.execute(ListNodeCatalogCommand(tenant_id=tenant_id))
    return {
        "definitions": [d.model_dump() for d in result.definitions],
        "category_tree": result.category_tree,
        "categories": {
            key: info.model_dump()
            for key, info in result.categories.items()
        },
        "generated_at": result.generated_at,
        "total": result.total,
    }


@router.post("/node-catalog/details")
async def get_node_catalog_details_batch(
    payload: BatchRefsDto,
    usecase: GetNodeCatalogDetailsBatchUseCase = Depends(_get_batch_uc),
) -> Any:
    gated = _gated_out(payload.tenant_id)
    if gated is not None:
        return gated
    try:
        parsed_refs = [_parse_ref_or_raise(r) for r in payload.refs]
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": {"code": "INVALID_REF", "message": str(exc)}},
        )

    try:
        result = await usecase.execute(GetNodeCatalogDetailsBatchCommand(
            refs=parsed_refs,
            tenant_id=payload.tenant_id,
        ))
    except BatchSizeExceededError as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": {"code": "BATCH_TOO_LARGE", "message": str(exc)}},
        )

    return {
        "details": [d.model_dump() for d in result.details],
        "missing": [
            {"node_id": m.node_id, "spec_version": m.spec_version}
            for m in result.missing
        ],
    }


@router.get("/node-catalog/{ref:path}")
async def get_node_catalog_detail(
    ref: str,
    tenant_id: str = "system",
    usecase: GetNodeCatalogDetailUseCase = Depends(_get_detail_uc),
) -> Any:
    gated = _gated_out(tenant_id)
    if gated is not None:
        return gated
    try:
        node_id, spec_version = parse_node_ref(ref)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"status": "error", "error": {"code": "INVALID_REF", "message": str(exc)}},
        )

    try:
        result = await usecase.execute(GetNodeCatalogDetailCommand(
            node_id=node_id,
            spec_version=spec_version,
        ))
    except ValueError as exc:
        # Spec-version mismatch or similar provider-side client error.
        return JSONResponse(
            status_code=422,
            content={
                "status": "error",
                "error": {"code": "VERSION_MISMATCH", "message": str(exc)},
            },
        )
    if result.detail is None:
        return JSONResponse(
            status_code=404,
            content={
                "status": "error",
                "error": {"code": "NOT_FOUND", "message": f"node {ref!r} not found"},
            },
        )
    return result.detail.model_dump()


def _parse_ref_or_raise(ref_str: str) -> NodeRef:
    node_id, version = parse_node_ref(ref_str)
    return NodeRef(node_id=node_id, spec_version=version)
