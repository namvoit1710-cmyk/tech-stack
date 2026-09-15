"""REST API for Environments / Config Variables (SA-2365).

Mounted under ``/api/v1/environments`` (see v1/__init__.py). Tenant is the
``X-Tenant-Id`` header (default ``system``); caller identity is ``X-User``.
Environments are tenant-scoped; secrets are never stored here — a credential-type
variable references the credentials vault by id.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from app.layer2_application.features.create_environment.use_cases.create_environment_use_case import (
    CreateEnvironmentCommand,
    CreateEnvironmentUseCase,
)
from app.layer2_application.features.delete_environment.use_cases.delete_environment_use_case import (
    DeleteEnvironmentCommand,
    DeleteEnvironmentUseCase,
)
from app.layer2_application.features.get_environment.use_cases.get_environment_use_case import (
    GetEnvironmentCommand,
    GetEnvironmentUseCase,
)
from app.layer2_application.features.list_environments.use_cases.list_environments_use_case import (
    ListEnvironmentsCommand,
    ListEnvironmentsUseCase,
)
from app.layer2_application.features.update_environment.use_cases.update_environment_use_case import (
    UpdateEnvironmentCommand,
    UpdateEnvironmentUseCase,
)
from app.layer2_application.features.list_workflows_for_environment.use_cases.list_workflows_for_environment_use_case import (
    ListWorkflowsForEnvironmentCommand,
    ListWorkflowsForEnvironmentUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.environment_dtos import (
    CreateEnvironmentRequest,
    UpdateEnvironmentRequest,
    variable_dtos_to_dicts,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    error_response,
    result_or_error,
)

router = APIRouter()


def _create_uc(request: Request) -> CreateEnvironmentUseCase:
    return request.app.state.container["create_environment"]


def _list_uc(request: Request) -> ListEnvironmentsUseCase:
    return request.app.state.container["list_environments"]


def _get_uc(request: Request) -> GetEnvironmentUseCase:
    return request.app.state.container["get_environment"]


def _update_uc(request: Request) -> UpdateEnvironmentUseCase:
    return request.app.state.container["update_environment"]


def _delete_uc(request: Request) -> DeleteEnvironmentUseCase:
    return request.app.state.container["delete_environment"]


def _list_workflows_uc(request: Request) -> ListWorkflowsForEnvironmentUseCase:
    return request.app.state.container["list_workflows_for_environment"]


@router.post("")
async def create_environment(
    payload: CreateEnvironmentRequest,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: CreateEnvironmentUseCase = Depends(_create_uc),
) -> Any:
    result = await usecase.execute(
        CreateEnvironmentCommand(
            tenant_id=x_tenant_id,
            section=payload.section,
            name=payload.name,
            variables=variable_dtos_to_dicts(payload.variables),
            created_by=x_user,
        )
    )
    return result_or_error(result)


@router.get("")
async def list_environments(
    section: str = Query(default=""),
    name_contains: str = Query(default=""),
    include_inactive: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    top: int = Query(default=100, ge=1, le=500),
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: ListEnvironmentsUseCase = Depends(_list_uc),
) -> Any:
    result = await usecase.execute(
        ListEnvironmentsCommand(
            tenant_id=x_tenant_id,
            section=section,
            name_contains=name_contains,
            include_inactive=include_inactive,
            skip=skip,
            top=top,
        )
    )
    return result_or_error(result)


@router.get("/{env_id}/workflows")
async def list_workflows_for_environment(
    env_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: ListWorkflowsForEnvironmentUseCase = Depends(_list_workflows_uc),
) -> Any:
    """SA-2450 W11 — reverse lookup: which workflows reference this environment
    (used by the SA-2365 delete-confirm)."""
    result = await usecase.execute(
        ListWorkflowsForEnvironmentCommand(tenant_id=x_tenant_id, environment_id=env_id)
    )
    return result_or_error(result)


@router.get("/{env_id}")
async def get_environment(
    env_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: GetEnvironmentUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(
        GetEnvironmentCommand(tenant_id=x_tenant_id, id=env_id)
    )
    return result_or_error(result)


@router.patch("/{env_id}")
async def update_environment(
    env_id: str,
    payload: UpdateEnvironmentRequest,
    if_match: str = Header(alias="If-Match", default=""),
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: UpdateEnvironmentUseCase = Depends(_update_uc),
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
        UpdateEnvironmentCommand(
            tenant_id=x_tenant_id,
            id=env_id,
            expected_version=expected_version,
            updated_by=x_user,
            new_section=payload.section,
            new_name=payload.name,
            new_variables=(
                variable_dtos_to_dicts(payload.variables)
                if payload.variables is not None
                else None
            ),
        )
    )
    return result_or_error(result)


@router.delete("/{env_id}")
async def delete_environment(
    env_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: DeleteEnvironmentUseCase = Depends(_delete_uc),
) -> Any:
    result = await usecase.execute(
        DeleteEnvironmentCommand(tenant_id=x_tenant_id, id=env_id, deleted_by=x_user)
    )
    return result_or_error(result)
