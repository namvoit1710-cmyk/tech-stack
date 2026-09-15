"""Worker-facing resolve endpoints for the gateway.

Two ways to address the same function; both return the concrete call-spec (incl.
the DECRYPTED auth header) for the gateway-worker to proxy a request:

  POST /api/v1/gateway/functions/{function_id}/_resolve   — by id
  POST /api/v1/gateway/functions/_resolve                 — by {app, function} name

The id is minted per-environment, so a workflow that pins one cannot be promoted
dev → qa → prod unchanged; the name pair is stable and is what the builder should
persist. Both live in THIS file on purpose: it is the only place that exposes a
materialized credential (worker-only — trust-the-network in P2; admin-gating is
P5). Keep it that way — do not add a resolve route to the admin CRUD controller.
Mounted under `/gateway/functions`.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.layer2_application.features.resolve_gateway_function.use_cases.resolve_gateway_function_use_case import (
    ResolveGatewayFunctionCommand,
    ResolveGatewayFunctionUseCase,
)
from app.layer3_adapters.controllers.restful.v1.dtos.gateway_dtos import (
    ResolveGatewayFunctionByNameRequest,
)
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import (
    result_or_error,
)

router = APIRouter()


def _resolve_uc(request: Request) -> ResolveGatewayFunctionUseCase:
    return request.app.state.container["resolve_gateway_function"]


# Declared BEFORE the id route: `/_resolve` is one segment and `/{function_id}/_resolve`
# is two, so they cannot collide — but keeping the literal first makes that obvious.
@router.post("/_resolve")
async def resolve_gateway_function_by_name(
    payload: ResolveGatewayFunctionByNameRequest,
    usecase: ResolveGatewayFunctionUseCase = Depends(_resolve_uc),
) -> Any:
    result = await usecase.execute(
        ResolveGatewayFunctionCommand(
            app_name=payload.app, function_name=payload.function
        )
    )
    return result_or_error(result)


@router.post("/{function_id}/_resolve")
async def resolve_gateway_function(
    function_id: str,
    usecase: ResolveGatewayFunctionUseCase = Depends(_resolve_uc),
) -> Any:
    result = await usecase.execute(
        ResolveGatewayFunctionCommand(function_id=function_id)
    )
    return result_or_error(result)
