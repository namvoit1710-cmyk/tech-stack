"""REST API for Section variable KEY SCHEMAs (SA-2471).

Mounted under ``/api/v1/env-sections`` (see v1/__init__.py). Tenant is the
``X-Tenant-Id`` header (default ``system``); caller identity is ``X-User``.

A section schema holds the shared list of ``{key, type}`` every Environment in a
Section must carry (SA-2365 environments own only the per-env values). Creating /
updating a schema RECONCILES the section's environments; deleting one cascades to
them. Responses use the house ``{status, data}`` envelope via ``result_or_error``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from app.layer2_application.features.create_env_section_schema.use_cases.create_env_section_schema_use_case import (
    CreateEnvSectionSchemaCommand,
    CreateEnvSectionSchemaUseCase,
)
from app.layer2_application.features.delete_env_section_schema.use_cases.delete_env_section_schema_use_case import (
    DeleteEnvSectionSchemaCommand,
    DeleteEnvSectionSchemaUseCase,
)
from app.layer2_application.features.get_env_section_schema.use_cases.get_env_section_schema_use_case import (
    GetEnvSectionSchemaCommand,
    GetEnvSectionSchemaUseCase,
)
from app.layer2_application.features.list_env_section_schema.use_cases.list_env_section_schema_use_case import (
    ListEnvSectionSchemaCommand,
    ListEnvSectionSchemaUseCase,
)
from app.layer2_application.features.update_env_section_schema.use_cases.update_env_section_schema_use_case import (
    UpdateEnvSectionSchemaCommand,
    UpdateEnvSectionSchemaUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.env_section_schema_dtos import (
    CreateEnvSectionSchemaRequest,
    UpdateEnvSectionSchemaRequest,
    key_dtos_to_dicts,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    error_response,
    result_or_error,
)

router = APIRouter()


def _create_uc(request: Request) -> CreateEnvSectionSchemaUseCase:
    return request.app.state.container["create_env_section_schema"]


def _list_uc(request: Request) -> ListEnvSectionSchemaUseCase:
    return request.app.state.container["list_env_section_schema"]


def _get_uc(request: Request) -> GetEnvSectionSchemaUseCase:
    return request.app.state.container["get_env_section_schema"]


def _update_uc(request: Request) -> UpdateEnvSectionSchemaUseCase:
    return request.app.state.container["update_env_section_schema"]


def _delete_uc(request: Request) -> DeleteEnvSectionSchemaUseCase:
    return request.app.state.container["delete_env_section_schema"]


@router.post("")
async def create_env_section_schema(
    payload: CreateEnvSectionSchemaRequest,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: CreateEnvSectionSchemaUseCase = Depends(_create_uc),
) -> Any:
    result = await usecase.execute(
        CreateEnvSectionSchemaCommand(
            tenant_id=x_tenant_id,
            section=payload.section,
            keys=key_dtos_to_dicts(payload.keys),
            created_by=x_user,
        )
    )
    return result_or_error(result)


@router.get("")
async def list_env_section_schemas(
    section: str = Query(default=""),
    include_inactive: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    top: int = Query(default=100, ge=1, le=500),
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: ListEnvSectionSchemaUseCase = Depends(_list_uc),
) -> Any:
    result = await usecase.execute(
        ListEnvSectionSchemaCommand(
            tenant_id=x_tenant_id,
            section=section,
            include_inactive=include_inactive,
            skip=skip,
            top=top,
        )
    )
    return result_or_error(result)


@router.get("/{schema_id}")
async def get_env_section_schema(
    schema_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: GetEnvSectionSchemaUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(
        GetEnvSectionSchemaCommand(tenant_id=x_tenant_id, id=schema_id)
    )
    if result.error:
        return result_or_error(result)
    # Flatten: data == the schema itself (id, section, keys, version,
    # environment_count, ...) per the SA-2471 REST contract.
    return result.section_schema


@router.patch("/{schema_id}")
async def update_env_section_schema(
    schema_id: str,
    payload: UpdateEnvSectionSchemaRequest,
    if_match: str = Header(alias="If-Match", default=""),
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: UpdateEnvSectionSchemaUseCase = Depends(_update_uc),
) -> Any:
    try:
        expected_version = int(if_match)
    except (TypeError, ValueError):
        return error_response(
            "PRECONDITION_REQUIRED",
            "If-Match header is required and must be an integer version.",
            status_code=409,
        )
    result = await usecase.execute(
        UpdateEnvSectionSchemaCommand(
            tenant_id=x_tenant_id,
            id=schema_id,
            expected_version=expected_version,
            updated_by=x_user,
            new_section=payload.section,
            new_keys=(
                key_dtos_to_dicts(payload.keys) if payload.keys is not None else None
            ),
        )
    )
    return result_or_error(result)


@router.delete("/{schema_id}")
async def delete_env_section_schema(
    schema_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: DeleteEnvSectionSchemaUseCase = Depends(_delete_uc),
) -> Any:
    result = await usecase.execute(
        DeleteEnvSectionSchemaCommand(tenant_id=x_tenant_id, id=schema_id, deleted_by=x_user)
    )
    return result_or_error(result)
