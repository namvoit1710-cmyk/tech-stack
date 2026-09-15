"""Trigger registration CRUD (SA-2059 B7/B9) — Settings → Triggers.

Manages inbound webhook **registrations** (per SOURCE, not per workflow) and
exposes their recent **deliveries** log. A registration = path + source +
secret + emit_topic; its URL fans out to N workflows via the topic. This is the
ingress side; the workflow side (Event trigger nodes) subscribes independently.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.layer1_domain.entities.webhook_registration import WebhookRegistration
from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response
from app.layer2_application.repositories.webhook_registration_repository_interface import (
    ListRegistrationsFilter,
)

router = APIRouter()


def _repo(request: Request):
    return request.app.state.container.get("webhook_registration_repo")


# ponytail: emit_topic is a PRODUCE target, not a subscription. Publishing to a
# wildcard ('*', '#', '>') or a blank/spaced name fails deep in the broker with
# a cryptic _UNKNOWN_TOPIC 502 at trigger time — reject it here as a clear 422.
_TOPIC_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _bad_topic(topic: str) -> str | None:
    if not (topic or "").strip():
        return "emit_topic is required"
    if not _TOPIC_RE.match(topic):
        return f"emit_topic '{topic}' is not a valid publish topic (allowed: letters, digits, . _ - ; no wildcards or spaces)"
    return None


class CreateRegistrationRequest(BaseModel):
    name: str
    path: str
    source: str = "generic"
    secret: str = ""
    hmac_secret: str = ""
    emit_topic: str
    ip_allowlist: list[str] = []
    integration_actor: str = ""
    tenant_id: str = "system"
    enabled: bool = True


class UpdateRegistrationRequest(BaseModel):
    name: str | None = None
    source: str | None = None
    secret: str | None = None
    hmac_secret: str | None = None
    emit_topic: str | None = None
    ip_allowlist: list[str] | None = None
    integration_actor: str | None = None
    enabled: bool | None = None


def _public(reg: WebhookRegistration) -> dict[str, Any]:
    """Registration view for the FE — secrets are never echoed back."""
    d = dataclasses.asdict(reg)
    d["has_secret"] = bool(d.pop("secret", ""))
    d["has_hmac_secret"] = bool(d.pop("hmac_secret", ""))
    return d


@router.post("")
async def create_registration(payload: CreateRegistrationRequest, request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    if (msg := _bad_topic(payload.emit_topic)) is not None:
        return error_response("BAD_REQUEST", msg, status_code=422)
    # F7: an ingress with neither a shared secret nor an HMAC secret is open —
    # anyone who knows the path can publish. Require at least one verifier.
    if not (payload.secret or payload.hmac_secret):
        return error_response(
            "BAD_REQUEST",
            "a shared secret or HMAC secret is required (an unverified registration is an open ingress)",
            status_code=422,
        )
    # F8: paths are a GLOBAL namespace. Ingress resolves get_by_path(path) with no
    # tenant (an inbound POST carries no trustworthy tenant discriminator), so a
    # path must be unique across ALL tenants, not just within the caller's.
    if repo.get_by_path(payload.path) is not None:
        return error_response("CONFLICT", f"Path '{payload.path}' already registered", status_code=409)
    reg = WebhookRegistration(
        name=payload.name, path=payload.path, source=payload.source,
        secret=payload.secret, hmac_secret=payload.hmac_secret,
        emit_topic=payload.emit_topic, ip_allowlist=payload.ip_allowlist,
        integration_actor=payload.integration_actor, tenant_id=payload.tenant_id,
        enabled=payload.enabled,
    )
    repo.save(reg)
    return {"status": "ok", "data": _public(reg)}


@router.get("")
async def list_registrations(request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    q = request.query_params
    page = repo.list_paged(ListRegistrationsFilter(
        tenant_id=q.get("tenant_id"), source=q.get("source"),
        skip=int(q.get("skip", 0)), top=int(q.get("top", 50)),
    ))
    # ponytail: envelope must be exactly {status,data[,error]} — a top-level
    # "total" defeats ResponseWrapperMiddleware's already-wrapped guard and gets
    # double-wrapped. No pagination UI needs the count; add it under data as
    # {items,total} (and read .data.items on the FE) when paging lands.
    return {"status": "ok", "data": [_public(r) for r in page.items]}


@router.get("/{registration_id}")
async def get_registration(registration_id: str, request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    reg = repo.get_by_id(registration_id)
    if reg is None:
        return error_response("NOT_FOUND", f"Registration '{registration_id}' not found", status_code=404)
    return {"status": "ok", "data": _public(reg)}


@router.patch("/{registration_id}")
async def update_registration(registration_id: str, payload: UpdateRegistrationRequest, request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    reg = repo.get_by_id(registration_id)
    if reg is None:
        return error_response("NOT_FOUND", f"Registration '{registration_id}' not found", status_code=404)
    if payload.emit_topic is not None and (msg := _bad_topic(payload.emit_topic)) is not None:
        return error_response("BAD_REQUEST", msg, status_code=422)
    # F7: don't let an update strip the last verifier and leave an open ingress.
    # A field left unset (None) keeps the stored value; "" explicitly clears it.
    new_secret = reg.secret if payload.secret is None else payload.secret
    new_hmac = reg.hmac_secret if payload.hmac_secret is None else payload.hmac_secret
    if not (new_secret or new_hmac):
        return error_response(
            "BAD_REQUEST",
            "a shared secret or HMAC secret is required (an unverified registration is an open ingress)",
            status_code=422,
        )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(reg, field, value)
    repo.save(reg)
    return {"status": "ok", "data": _public(reg)}


@router.delete("/{registration_id}")
async def delete_registration(registration_id: str, request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    repo.delete(registration_id)
    return {"status": "ok", "data": {"id": registration_id, "deleted": True}}


def _derive_event_columns(payload: str | None) -> dict[str, str]:
    """Extract two small, list-safe columns from the raw payload so the
    deliveries table can show them WITHOUT the client fetching each row's full
    payload. Best-effort — a non-JSON body, a non-dict body, or a source that
    doesn't carry these fields (generic/GitHub/Stripe) yields ``""``.

    ``webhook_event`` prefers Jira's ``webhookEvent`` (e.g. ``jira:issue_updated``)
    then ``issue_event_type_name``. ``issue_key`` reads the nested Jira shape
    ``issue.key`` (e.g. ``SA-1234``), falling back to a flat ``issue_key`` for
    Jira Automation custom bodies.
    """
    empty = {"webhook_event": "", "issue_key": ""}
    if not payload:
        return empty
    try:
        body = json.loads(payload)
    except (ValueError, TypeError):
        return empty
    if not isinstance(body, dict):
        return empty
    issue = body.get("issue")
    issue_key = str(issue.get("key") or "") if isinstance(issue, dict) else ""
    if not issue_key:
        issue_key = str(body.get("issue_key") or "")
    webhook_event = str(body.get("webhookEvent") or body.get("issue_event_type_name") or "")
    return {"webhook_event": webhook_event, "issue_key": issue_key}


def _delivery_summary(d) -> dict[str, Any]:
    """List view — everything but the (potentially large) raw payload, which is
    fetched on demand per row via the detail route below. Two small derived
    columns (``webhook_event``, ``issue_key``) are pulled from the payload here
    so the FE can render them without a per-row detail fetch."""
    row = dataclasses.asdict(d)
    payload = row.pop("payload", None)
    row.update(_derive_event_columns(payload))
    return row


@router.get("/{registration_id}/deliveries")
async def list_deliveries(registration_id: str, request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    limit = int(request.query_params.get("limit", 50))
    deliveries = repo.list_deliveries(registration_id, limit)
    return {"status": "ok", "data": [_delivery_summary(d) for d in deliveries]}


@router.get("/{registration_id}/deliveries/{delivery_id}")
async def get_delivery(registration_id: str, delivery_id: str, request: Request) -> Any:
    repo = _repo(request)
    if repo is None:
        return error_response("SERVICE_UNAVAILABLE", "Registrations unavailable", status_code=503)
    delivery = repo.get_delivery(registration_id, delivery_id)
    if delivery is None:
        return error_response("NOT_FOUND", f"Delivery '{delivery_id}' not found", status_code=404)
    return {"status": "ok", "data": dataclasses.asdict(delivery)}
