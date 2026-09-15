"""Webhook management CRUD endpoints.

Separate from the webhook gateway (POST /webhooks/{path}) which triggers
workflow runs. This controller manages webhook configurations.
"""

import dataclasses

from fastapi import APIRouter, Depends, Request

from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response, result_or_error
from app.layer3_adapters.controllers.restful.v1.dtos.odata_params import (
    parse_odata_params, parse_odata_filter, odata_page_response,
)
from app.layer2_application.features.create_webhook.use_cases.create_webhook_use_case import (
    CreateWebhookUseCase, CreateWebhookCommand,
)
from app.layer2_application.features.list_webhooks.use_cases.list_webhooks_use_case import (
    ListWebhooksUseCase, ListWebhooksCommand,
)
from app.layer2_application.features.get_webhook.use_cases.get_webhook_use_case import (
    GetWebhookUseCase, GetWebhookCommand,
)
from app.layer2_application.features.update_webhook.use_cases.update_webhook_use_case import (
    UpdateWebhookUseCase, UpdateWebhookCommand,
)
from app.layer2_application.features.delete_webhook.use_cases.delete_webhook_use_case import (
    DeleteWebhookUseCase, DeleteWebhookCommand,
)
from app.layer2_application.features.list_webhook_deliveries.use_cases.list_webhook_deliveries_use_case import (
    ListWebhookDeliveriesUseCase, ListWebhookDeliveriesCommand,
)

from pydantic import BaseModel
from typing import Any


# --- DI helpers ---

def _get_create(request: Request) -> CreateWebhookUseCase:
    return request.app.state.container["create_webhook"]

def _get_list(request: Request) -> ListWebhooksUseCase:
    return request.app.state.container["list_webhooks"]

def _get_get(request: Request) -> GetWebhookUseCase:
    return request.app.state.container["get_webhook"]

def _get_update(request: Request) -> UpdateWebhookUseCase:
    return request.app.state.container["update_webhook"]

def _get_delete(request: Request) -> DeleteWebhookUseCase:
    return request.app.state.container["delete_webhook"]

def _get_deliveries(request: Request) -> ListWebhookDeliveriesUseCase:
    return request.app.state.container["list_webhook_deliveries"]


# --- Request DTOs ---

class CreateWebhookRequest(BaseModel):
    workflow_id: str
    name: str
    url: str
    secret: str = ""
    events: list[str] = []
    tenant_id: str = "system"


class UpdateWebhookRequest(BaseModel):
    name: str | None = None
    url: str | None = None
    secret: str | None = None
    events: list[str] | None = None
    is_active: bool | None = None


# --- Router ---

router = APIRouter()


@router.post("")
async def create_webhook(
    payload: CreateWebhookRequest,
    usecase: CreateWebhookUseCase = Depends(_get_create),
) -> Any:
    command = CreateWebhookCommand(
        workflow_id=payload.workflow_id,
        name=payload.name,
        url=payload.url,
        secret=payload.secret,
        events=payload.events,
        tenant_id=payload.tenant_id,
    )
    result = await usecase.execute(command)
    return result_or_error(result)


@router.get("")
async def list_webhooks(
    request: Request,
    usecase: ListWebhooksUseCase = Depends(_get_list),
) -> Any:
    params = parse_odata_params(request)
    filters = parse_odata_filter(params.filter)
    command = ListWebhooksCommand(
        # filters is an ODataFilterResult (dataclass) — equality terms live in
        # .eq; it has no .get(). Reading filters.get(...) AttributeErrors → 500.
        workflow_id=filters.eq.get("workflow_id") or request.query_params.get("workflow_id"),
        tenant_id=filters.eq.get("tenant_id") or request.query_params.get("tenant_id"),
        skip=params.skip,
        top=params.top,
    )
    result = await usecase.execute(command)
    return odata_page_response(result.items, result.total, params)


@router.get("/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    usecase: GetWebhookUseCase = Depends(_get_get),
) -> Any:
    command = GetWebhookCommand(webhook_id=webhook_id)
    result = await usecase.execute(command)
    return result_or_error(result)


@router.patch("/{webhook_id}")
async def update_webhook(
    webhook_id: str,
    payload: UpdateWebhookRequest,
    usecase: UpdateWebhookUseCase = Depends(_get_update),
) -> Any:
    command = UpdateWebhookCommand(
        webhook_id=webhook_id,
        name=payload.name,
        url=payload.url,
        secret=payload.secret,
        events=payload.events,
        is_active=payload.is_active,
    )
    result = await usecase.execute(command)
    return result_or_error(result)


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: str,
    usecase: DeleteWebhookUseCase = Depends(_get_delete),
) -> Any:
    command = DeleteWebhookCommand(webhook_id=webhook_id)
    result = await usecase.execute(command)
    return result_or_error(result)


@router.get("/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    webhook_id: str,
    request: Request,
    usecase: ListWebhookDeliveriesUseCase = Depends(_get_deliveries),
) -> Any:
    params = parse_odata_params(request)
    command = ListWebhookDeliveriesCommand(
        webhook_id=webhook_id,
        skip=params.skip,
        top=params.top,
    )
    result = await usecase.execute(command)
    return odata_page_response(result.items, result.total, params)
