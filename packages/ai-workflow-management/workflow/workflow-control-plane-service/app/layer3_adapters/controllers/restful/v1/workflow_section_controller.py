"""REST API for the workflow→section binding (SA-2605).

Mounted under ``/api/v1/workflows`` (see v1/__init__.py) alongside the workflow
controller. A workflow binds to exactly one section; at run time it may use any
environment of that section (no per-flow env subset — supersedes SA-2450).

    GET  /api/v1/workflows/{workflow_id}/section
    PUT  /api/v1/workflows/{workflow_id}/section   {section|sectionId}

Tenant is ``X-Tenant-Id`` (default ``system``); caller identity is ``X-User``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request

from app.layer2_application.features.get_workflow_section.use_cases.get_workflow_section_use_case import (
    GetWorkflowSectionCommand,
    GetWorkflowSectionUseCase,
)
from app.layer2_application.features.set_workflow_section.use_cases.set_workflow_section_use_case import (
    SetWorkflowSectionCommand,
    SetWorkflowSectionUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.section_binding_dtos import (
    SetWorkflowSectionRequest,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    result_or_error,
)

router = APIRouter()


def _get_uc(request: Request) -> GetWorkflowSectionUseCase:
    return request.app.state.container["get_workflow_section"]


def _set_uc(request: Request) -> SetWorkflowSectionUseCase:
    return request.app.state.container["set_workflow_section"]


@router.get("/{workflow_id}/section")
async def get_workflow_section(
    workflow_id: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: GetWorkflowSectionUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(
        GetWorkflowSectionCommand(tenant_id=x_tenant_id, workflow_id=workflow_id)
    )
    return result_or_error(result)


@router.put("/{workflow_id}/section")
async def set_workflow_section(
    workflow_id: str,
    payload: SetWorkflowSectionRequest,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: SetWorkflowSectionUseCase = Depends(_set_uc),
) -> Any:
    result = await usecase.execute(
        SetWorkflowSectionCommand(
            tenant_id=x_tenant_id,
            workflow_id=workflow_id,
            section=payload.section,
            updated_by=x_user,
        )
    )
    return result_or_error(result)
