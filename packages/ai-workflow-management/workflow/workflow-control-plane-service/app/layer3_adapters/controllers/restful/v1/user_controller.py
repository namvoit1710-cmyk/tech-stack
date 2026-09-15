"""Known-users API (SA-2576).

Mounted under ``/api/v1/users``. ``GET /users`` returns everyone who has logged
into the workflow app — the source for the workflow owner picker. Guarded by the
auth-enforcement middleware like every other ``/api/v1/*`` route.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.layer2_application.features.list_users.use_cases.list_users_use_case import (
    ListUsersUseCase,
)

router = APIRouter()


def _list_users_uc(request: Request) -> ListUsersUseCase:
    return request.app.state.container["list_users"]


@router.get("")
async def list_users(usecase: ListUsersUseCase = Depends(_list_users_uc)) -> Any:
    result = await usecase.execute()
    return {"items": result.items, "total": result.total}
