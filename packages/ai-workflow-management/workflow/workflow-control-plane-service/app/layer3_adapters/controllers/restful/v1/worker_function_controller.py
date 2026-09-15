"""Controller for worker function discovery and invocation (proxy to Worker Executor)."""

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response

router = APIRouter()


class InvokeFunctionRequest(BaseModel):
    params: dict[str, Any] = {}
    # SA-1551: when the FE loads options for a credential-dependent field, it sends
    # the run tenant so the vault resolve is tenant-scoped (TENANT_BLEED). Optional —
    # design-time invocations without a credential_ref don't need it.
    tenant_id: str = "system"


def _get_client(request: Request) -> Any:
    return request.app.state.container.get("_worker_registry_client")


def _get_vault_client(request: Request) -> Any:
    return request.app.state.container.get("_vault_credential_client")


async def _resolve_credentials_into_params(
    request: Request, params: dict[str, Any], tenant_id: str
) -> dict[str, Any] | JSONResponse:
    """Fold a design-time ``credential_ref`` into ``params.__credentials`` (SA-1551).

    The FE serverOptions POST body carries the chosen ``credential_ref`` (and any
    dependency values) under ``params.args``. To let a design-time function
    (e.g. ``list_teams``) authenticate exactly like it does at run time, resolve the
    ref against the vault here — server-side, tenant-scoped — and inject the
    ``{ref: {type, config}}`` map as ``params.__credentials`` before forwarding.
    Secrets never round-trip through the browser; only the id does.

    Returns the (possibly enriched) params dict, or a JSONResponse on a resolve error.
    """
    args = params.get("args") if isinstance(params.get("args"), dict) else params
    ref = args.get("credential_ref") if isinstance(args, dict) else None
    if not ref:
        return params  # no credential dependency — nothing to resolve

    vault = _get_vault_client(request)
    if vault is None:
        # Vault unconfigured — fail closed rather than invoke without auth.
        return JSONResponse(status_code=503, content={
            "status": "error",
            "error": {"code": "VAULT_UNAVAILABLE",
                      "message": "Credential resolution is not available on this environment."},
        })

    try:
        entry = await vault.resolve_by_id(tenant_id, ref, None)
    except Exception as exc:  # CredentialNotFound / CredentialResolveAuthError / transport
        name = type(exc).__name__
        if name == "CredentialResolveAuthError":
            return JSONResponse(status_code=403, content={
                "status": "error",
                "error": {"code": "CREDENTIAL_FORBIDDEN",
                          "message": "Not authorized to use this credential in this tenant."},
            })
        if name == "CredentialNotFound":
            return JSONResponse(status_code=404, content={
                "status": "error",
                "error": {"code": "CREDENTIAL_NOT_FOUND",
                          "message": "The selected credential no longer exists."},
            })
        # Do NOT echo the exception detail — it may carry sensitive material.
        return JSONResponse(status_code=502, content={
            "status": "error",
            "error": {"code": "CREDENTIAL_RESOLVE_FAILED",
                      "message": "Could not resolve the selected credential."},
        })

    enriched = dict(params)
    creds = dict(enriched.get("__credentials") or {})
    creds[ref] = {"type": entry["type"], "config": entry["config"]}
    enriched["__credentials"] = creds
    # Hand the worker a CANONICAL shape: `__credentials` is written flat (above),
    # so `credential_ref` must be flat too. The FE nests it — buildPostBody does
    # `params.args = {...depParams}` — but `resolve_effective_auth` reads
    # `params["credential_ref"]` at the top level, falls back to "default", and
    # finds nothing, so every design-time function that authenticates fails closed
    # with "no credential was found". Mirroring the ref up fixes that for every
    # worker at once instead of each one re-deriving where the FE put it.
    # setdefault: a caller that already sent it flat wins.
    enriched.setdefault("credential_ref", ref)
    return enriched


@router.get("/{worker_type}/functions")
def list_functions(worker_type: str, request: Request) -> Any:
    client = _get_client(request)
    if not client:
        return error_response("SERVICE_UNAVAILABLE", "Worker registry client not configured", status_code=503)
    functions = client.list_worker_functions(worker_type)
    return functions


@router.post("/{worker_type}/functions/{function_name}/invoke")
async def invoke_function(worker_type: str, function_name: str, payload: InvokeFunctionRequest, request: Request) -> Any:
    client = _get_client(request)
    if not client:
        return error_response("SERVICE_UNAVAILABLE", "Worker registry client not configured", status_code=503)

    params = await _resolve_credentials_into_params(request, payload.params, payload.tenant_id)
    if isinstance(params, JSONResponse):
        return params  # resolve error (403/404/502/503)

    # invoke_worker_function is sync; dispatch to a thread so the resolve await above
    # doesn't force the whole request onto a blocking call on the event loop.
    result = await asyncio.to_thread(
        client.invoke_worker_function, worker_type, function_name, params
    )
    if isinstance(result, dict) and result.get("status") == "error":
        return JSONResponse(status_code=400, content={
            "status": "error",
            "error": {"code": "FUNCTION_ERROR", "message": result.get("error", "Unknown error")},
        })
    return result

# SA-1551: force WCP rebuild+redeploy. The original deploy (#722, tag solace-547)
# failed to stage on a CF org memory-quota error (concurrent-deploy pileup), so the
# running control-plane never picked up the credential-aware invoke resolution above.
# Subsequent deploys (solace-548..550) succeeded but did not touch WCP, so change-
# discovery skipped it. This no-op touch puts WCP back in the deploy diff.
