"""REST API for the workflow↔section binding (SA-2450).

Mounted under ``/api/v1/workflows`` (see v1/__init__.py) alongside the workflow
controller. A workflow binds to exactly one section + a subset (≥1) of that
section's environments; only those are offered when the workflow runs.

    GET  /api/v1/workflows/{workflow_id}/environment-assignment
    PUT  /api/v1/workflows/{workflow_id}/environment-assignment  {section, environmentIds}

Tenant is ``X-Tenant-Id`` (default ``system``); caller identity is ``X-User``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request

from app.layer2_application.features.get_workflow_environment_assignment.use_cases.get_workflow_environment_assignment_use_case import (
    GetWorkflowEnvironmentAssignmentCommand,
    GetWorkflowEnvironmentAssignmentUseCase,
)
from app.layer2_application.features.set_workflow_environment_assignment.use_cases.set_workflow_environment_assignment_use_case import (
    SetWorkflowEnvironmentAssignmentCommand,
    SetWorkflowEnvironmentAssignmentUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.workflow_environment_dtos import (
    SetWorkflowEnvironmentAssignmentRequest,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    result_or_error,
)

router = APIRouter()


def _get_uc(request: Request) -> GetWorkflowEnvironmentAssignmentUseCase:
    return request.app.state.container["get_workflow_environment_assignment"]


def _set_uc(request: Request) -> SetWorkflowEnvironmentAssignmentUseCase:
    return request.app.state.container["set_workflow_environment_assignment"]


@router.get("/{workflow_id}/environment-assignment")
async def get_workflow_environment_assignment(
    workflow_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: GetWorkflowEnvironmentAssignmentUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(
        GetWorkflowEnvironmentAssignmentCommand(
            tenant_id=x_tenant_id, workflow_id=workflow_id
        )
    )
    return result_or_error(result)


@router.put("/{workflow_id}/environment-assignment")
async def set_workflow_environment_assignment(
    workflow_id: str,
    payload: SetWorkflowEnvironmentAssignmentRequest,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: SetWorkflowEnvironmentAssignmentUseCase = Depends(_set_uc),
) -> Any:
    result = await usecase.execute(
        SetWorkflowEnvironmentAssignmentCommand(
            tenant_id=x_tenant_id,
            workflow_id=workflow_id,
            section=payload.section,
            environment_ids=payload.environment_ids,
            updated_by=x_user,
        )
    )
    return result_or_error(result)
