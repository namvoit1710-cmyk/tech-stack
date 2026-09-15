"""Admin REST API for the gateway app registry.

Mounted under ``/api/v1/gateway/apps`` (see v1/__init__.py). Caller identity is
the ``X-User`` header (defaults to ``system`` for local/mock tests). Admin-role
gating is a P5 follow-up.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from app.layer2_application.features.create_gateway_app.use_cases.create_gateway_app_use_case import (
    CreateGatewayAppCommand,
    CreateGatewayAppUseCase,
)
from app.layer2_application.features.delete_gateway_app.use_cases.delete_gateway_app_use_case import (
    DeleteGatewayAppCommand,
    DeleteGatewayAppUseCase,
)
from app.layer2_application.features.get_gateway_app.use_cases.get_gateway_app_use_case import (
    GetGatewayAppCommand,
    GetGatewayAppUseCase,
)
from app.layer2_application.features.list_gateway_apps.use_cases.list_gateway_apps_use_case import (
    ListGatewayAppsCommand,
    ListGatewayAppsUseCase,
)
from app.layer2_application.features.test_gateway_app.use_cases.test_gateway_app_use_case import (
    TestGatewayAppCommand,
    TestGatewayAppUseCase,
)
from app.layer2_application.features.sync_gateway_app_openapi.use_cases.sync_gateway_app_openapi_use_case import (
    SyncGatewayAppOpenapiCommand,
    SyncGatewayAppOpenapiUseCase,
)
from app.layer2_application.features.update_gateway_app.use_cases.update_gateway_app_use_case import (
    UpdateGatewayAppCommand,
    UpdateGatewayAppUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.gateway_dtos import (
    CreateGatewayAppRequest,
    TestGatewayAppRequest,
    UpdateGatewayAppRequest,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    error_response,
    result_or_error,
)

router = APIRouter()


def _create_uc(request: Request) -> CreateGatewayAppUseCase:
    return request.app.state.container["create_gateway_app"]


def _get_uc(request: Request) -> GetGatewayAppUseCase:
    return request.app.state.container["get_gateway_app"]


def _list_uc(request: Request) -> ListGatewayAppsUseCase:
    return request.app.state.container["list_gateway_apps"]


def _update_uc(request: Request) -> UpdateGatewayAppUseCase:
    return request.app.state.container["update_gateway_app"]


def _delete_uc(request: Request) -> DeleteGatewayAppUseCase:
    return request.app.state.container["delete_gateway_app"]


def _test_uc(request: Request) -> TestGatewayAppUseCase:
    return request.app.state.container["test_gateway_app"]


def _sync_uc(request: Request) -> SyncGatewayAppOpenapiUseCase:
    return request.app.state.container["sync_gateway_app_openapi"]


@router.post("")
async def create_gateway_app(
    payload: CreateGatewayAppRequest,
    x_user: str = Header(default="system", alias="X-User"),
    usecase: CreateGatewayAppUseCase = Depends(_create_uc),
) -> Any:
    result = await usecase.execute(
        CreateGatewayAppCommand(
            name=payload.name,
            kind=payload.kind,
            base_url=payload.base_url,
            allowed_hosts=payload.allowed_hosts,
            auth_type=payload.auth_type,
            auth_config=payload.auth_config,
            description=payload.description,
            spec_url=payload.spec_url,
            created_by=x_user,
        )
    )
    return result_or_error(result)


@router.get("")
async def list_gateway_apps(
    name_contains: str = Query(default=""),
    include_inactive: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    top: int = Query(default=50, ge=1, le=200),
    usecase: ListGatewayAppsUseCase = Depends(_list_uc),
) -> Any:
    result = await usecase.execute(
        ListGatewayAppsCommand(
            name_contains=name_contains,
            include_inactive=include_inactive,
            skip=skip,
            top=top,
        )
    )
    return result_or_error(result)


@router.get("/{name}")
async def get_gateway_app(
    name: str,
    usecase: GetGatewayAppUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(GetGatewayAppCommand(name=name))
    return result_or_error(result)


@router.patch("/{name}")
async def update_gateway_app(
    name: str,
    payload: UpdateGatewayAppRequest,
    if_match: str = Header(alias="If-Match", default=""),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: UpdateGatewayAppUseCase = Depends(_update_uc),
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
        UpdateGatewayAppCommand(
            name=name,
            expected_version=expected_version,
            updated_by=x_user,
            new_name=payload.name,
            new_description=payload.description,
            new_base_url=payload.base_url,
            new_allowed_hosts=payload.allowed_hosts,
            new_auth_type=payload.auth_type,
            new_auth_config=payload.auth_config,
            new_spec_url=payload.spec_url,
        )
    )
    return result_or_error(result)


# Declared before ``/{name}`` so the two-segment form is matched first. They
# cannot actually collide (``/{name}`` is one segment), but keeping the more
# specific route above the catch-all is the safer habit.
@router.delete("/by-id/{app_id}")
async def delete_gateway_app_by_id(
    app_id: str,
    x_user: str = Header(default="system", alias="X-User"),
    usecase: DeleteGatewayAppUseCase = Depends(_delete_uc),
) -> Any:
    """Retire an app by ``id`` — the handle that always survives a URL.

    ``DELETE /{name}`` is the everyday route, but the name is a path segment
    and rows written before the addressability rule may carry names no route
    can match (``%2F`` is decoded before routing). Those rows are visible in
    ``GET /gateway/apps?include_inactive=true``, which carries the ``id``, so
    this route is the way to clear them. Idempotent: an already-inactive app
    reports ``deleted`` rather than 404.
    """
    result = await usecase.execute(
        DeleteGatewayAppCommand(id=app_id, deleted_by=x_user)
    )
    return result_or_error(result)


@router.delete("/{name}")
async def delete_gateway_app(
    name: str,
    x_user: str = Header(default="system", alias="X-User"),
    usecase: DeleteGatewayAppUseCase = Depends(_delete_uc),
) -> Any:
    result = await usecase.execute(
        DeleteGatewayAppCommand(name=name, deleted_by=x_user)
    )
    return result_or_error(result)


@router.post("/{name}/test")
async def test_gateway_app(
    name: str,
    payload: TestGatewayAppRequest | None = None,
    usecase: TestGatewayAppUseCase = Depends(_test_uc),
) -> Any:
    timeout_s = payload.timeout_s if payload else 5.0
    result = await usecase.execute(
        TestGatewayAppCommand(name=name, timeout_s=timeout_s)
    )
    return result_or_error(result)


@router.post("/{name}/sync")
async def sync_gateway_app(
    name: str,
    force: bool = Query(default=False),
    usecase: SyncGatewayAppOpenapiUseCase = Depends(_sync_uc),
) -> Any:
    """Re-fetch the app's OpenAPI spec now and reconcile its functions.

    ``force=true`` bypasses the unchanged-spec (hash) short-circuit.
    """
    result = await usecase.execute(
        SyncGatewayAppOpenapiCommand(app_name=name, force=force)
    )
    return result_or_error(result)
