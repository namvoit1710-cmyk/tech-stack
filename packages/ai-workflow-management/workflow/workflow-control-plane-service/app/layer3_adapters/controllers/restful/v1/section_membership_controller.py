"""REST API for the Section↔Environment membership (SA-2605, many-to-many).

Mounted under ``/api/v1/sections`` (see v1/__init__.py).

    GET  /api/v1/sections/memberships                 -> all sections' memberships
    GET  /api/v1/sections/{section}/environments      -> { section, environmentIds }
    PUT  /api/v1/sections/{section}/environments       {environmentIds}

Tenant is ``X-Tenant-Id`` (default ``system``); caller identity is ``X-User``.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request

from app.layer2_application.features.get_section_membership.use_cases.get_section_membership_use_case import (
    GetSectionMembershipCommand,
    GetSectionMembershipUseCase,
)
from app.layer2_application.features.set_section_membership.use_cases.set_section_membership_use_case import (
    SetSectionMembershipCommand,
    SetSectionMembershipUseCase,
)
from app.layer2_application.features.list_section_memberships.use_cases.list_section_memberships_use_case import (
    ListSectionMembershipsCommand,
    ListSectionMembershipsUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.section_binding_dtos import (
    SetSectionMembershipRequest,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    result_or_error,
)

router = APIRouter()


def _get_uc(request: Request) -> GetSectionMembershipUseCase:
    return request.app.state.container["get_section_membership"]


def _set_uc(request: Request) -> SetSectionMembershipUseCase:
    return request.app.state.container["set_section_membership"]


def _list_uc(request: Request) -> ListSectionMembershipsUseCase:
    return request.app.state.container["list_section_memberships"]


@router.get("/memberships")
async def list_section_memberships(
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: ListSectionMembershipsUseCase = Depends(_list_uc),
) -> Any:
    result = await usecase.execute(ListSectionMembershipsCommand(tenant_id=x_tenant_id))
    return result_or_error(result)


@router.get("/{section}/environments")
async def get_section_membership(
    section: str,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    usecase: GetSectionMembershipUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(
        GetSectionMembershipCommand(tenant_id=x_tenant_id, section=section)
    )
    return result_or_error(result)


@router.put("/{section}/environments")
async def set_section_membership(
    section: str,
    payload: SetSectionMembershipRequest,
    x_tenant_id: str = Header(default="system", alias="X-Tenant-Id"),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: SetSectionMembershipUseCase = Depends(_set_uc),
) -> Any:
    result = await usecase.execute(
        SetSectionMembershipCommand(
            tenant_id=x_tenant_id,
            section=section,
            environment_ids=payload.environment_ids,
            updated_by=x_user,
        )
    )
    return result_or_error(result)
