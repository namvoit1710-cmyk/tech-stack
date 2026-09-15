"""Admin REST API for functions of a gateway app.

Mounted under ``/api/v1/gateway/apps/{app_name}/functions`` (see v1/__init__.py).
``app_name`` is taken from the path; ``X-User`` is the caller identity.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request

from app.layer2_application.features.create_gateway_function.use_cases.create_gateway_function_use_case import (
    CreateGatewayFunctionCommand,
    CreateGatewayFunctionUseCase,
)
from app.layer2_application.features.delete_gateway_function.use_cases.delete_gateway_function_use_case import (
    DeleteGatewayFunctionCommand,
    DeleteGatewayFunctionUseCase,
)
from app.layer2_application.features.get_gateway_function.use_cases.get_gateway_function_use_case import (
    GetGatewayFunctionCommand,
    GetGatewayFunctionUseCase,
)
from app.layer2_application.features.list_gateway_functions.use_cases.list_gateway_functions_use_case import (
    ListGatewayFunctionsCommand,
    ListGatewayFunctionsUseCase,
)
from app.layer2_application.features.update_gateway_function.use_cases.update_gateway_function_use_case import (
    UpdateGatewayFunctionCommand,
    UpdateGatewayFunctionUseCase,
    _UNSET,
)
from app.layer3_adapters.controllers.restful.v1.dtos.gateway_dtos import (
    CreateGatewayFunctionRequest,
    UpdateGatewayFunctionRequest,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    error_response,
    result_or_error,
)

router = APIRouter()


def _create_uc(request: Request) -> CreateGatewayFunctionUseCase:
    return request.app.state.container["create_gateway_function"]


def _get_uc(request: Request) -> GetGatewayFunctionUseCase:
    return request.app.state.container["get_gateway_function"]


def _list_uc(request: Request) -> ListGatewayFunctionsUseCase:
    return request.app.state.container["list_gateway_functions"]


def _update_uc(request: Request) -> UpdateGatewayFunctionUseCase:
    return request.app.state.container["update_gateway_function"]


def _delete_uc(request: Request) -> DeleteGatewayFunctionUseCase:
    return request.app.state.container["delete_gateway_function"]


@router.post("")
async def create_gateway_function(
    app_name: str,
    payload: CreateGatewayFunctionRequest,
    x_user: str = Header(default="system", alias="X-User"),
    usecase: CreateGatewayFunctionUseCase = Depends(_create_uc),
) -> Any:
    result = await usecase.execute(
        CreateGatewayFunctionCommand(
            app_name=app_name,
            name=payload.name,
            method=payload.method,
            path_template=payload.path_template,
            input_params=payload.input_params,
            output_fields=payload.output_fields,
            description=payload.description,
            timeout_ms=payload.timeout_ms,
            icon=payload.icon,
            color=payload.color,
            tags=payload.tags,
            created_by=x_user,
        )
    )
    return result_or_error(result)


@router.get("")
async def list_gateway_functions(
    app_name: str,
    name_contains: str = Query(default=""),
    include_inactive: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    top: int = Query(default=50, ge=1, le=200),
    usecase: ListGatewayFunctionsUseCase = Depends(_list_uc),
) -> Any:
    result = await usecase.execute(
        ListGatewayFunctionsCommand(
            app_name=app_name,
            name_contains=name_contains,
            include_inactive=include_inactive,
            skip=skip,
            top=top,
        )
    )
    return result_or_error(result)


@router.get("/{name}")
async def get_gateway_function(
    app_name: str,
    name: str,
    usecase: GetGatewayFunctionUseCase = Depends(_get_uc),
) -> Any:
    result = await usecase.execute(
        GetGatewayFunctionCommand(app_name=app_name, name=name)
    )
    return result_or_error(result)


@router.patch("/{name}")
async def update_gateway_function(
    app_name: str,
    name: str,
    payload: UpdateGatewayFunctionRequest,
    if_match: str = Header(alias="If-Match", default=""),
    x_user: str = Header(default="system", alias="X-User"),
    usecase: UpdateGatewayFunctionUseCase = Depends(_update_uc),
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
        UpdateGatewayFunctionCommand(
            app_name=app_name,
            name=name,
            expected_version=expected_version,
            updated_by=x_user,
            new_name=payload.name,
            new_description=payload.description,
            new_method=payload.method,
            new_path_template=payload.path_template,
            # None means "leave alone" -> _UNSET; [] means "clear".
            new_input_params=(
                _UNSET if payload.input_params is None else payload.input_params
            ),
            new_output_fields=(
                _UNSET if payload.output_fields is None else payload.output_fields
            ),
            new_timeout_ms=payload.timeout_ms,
            new_icon=payload.icon,
            new_color=payload.color,
            new_tags=payload.tags,
        )
    )
    return result_or_error(result)


@router.delete("/{name}")
async def delete_gateway_function(
    app_name: str,
    name: str,
    x_user: str = Header(default="system", alias="X-User"),
    usecase: DeleteGatewayFunctionUseCase = Depends(_delete_uc),
) -> Any:
    result = await usecase.execute(
        DeleteGatewayFunctionCommand(app_name=app_name, name=name, deleted_by=x_user)
    )
    return result_or_error(result)
