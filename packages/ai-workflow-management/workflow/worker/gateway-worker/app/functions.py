"""WorkerFunctions exposed by the gateway-worker.

Invoked synchronously via the control-plane's invoke_function route:
  POST /api/v1/gateway-worker/functions/call_internal/invoke
  POST /api/v1/gateway-worker/functions/call_external/invoke

Gateway-level errors live INSIDE the success result as `{error: {code, message}}`
so the invoke envelope stays 200 (see workflow-gateway-design.md §2.4 — a
top-level `status: "error"` would collapse to `FUNCTION_ERROR` HTTP 400 and
flatten our distinct codes into one).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction

from app.layer1_domain.value_objects.external_url_policy import (
    ExternalUrlPolicy,
    HostNotAllowed,
    PrivateIpBlocked,
    SchemeNotAllowed,
)
from app.layer1_domain.value_objects.app_function_request import (
    InvokeAppFunctionParams,
    build_upstream_request,
)
from app.layer1_domain.value_objects.gateway_request import (
    CallExternalParams,
    CallInternalParams,
    GatewayResult,
    InvalidParams,
)
from app.layer4_frameworks.allowlist_loader import load_external_url_policy
from app.layer4_frameworks.registry_client import (
    AppFunctionResolveClient,
    InternalServiceRegistryClient,
    RegistryUnavailable,
    ServiceNotFound,
)
from app.layer4_frameworks.upstream_http import (
    ResponseTooLarge,
    UpstreamError,
    UpstreamHttpClient,
    UpstreamTimeout,
)


_RESERVED_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


def _strip_reserved(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in _RESERVED_HEADERS}


def _build_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _make_call_internal_handler(
    registry: InternalServiceRegistryClient,
    upstream: UpstreamHttpClient,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def call_internal(params: dict[str, Any]) -> dict[str, Any]:
        try:
            req = CallInternalParams.from_dict(params)
        except InvalidParams as exc:
            return GatewayResult.gateway_error("BAD_REQUEST", str(exc)).to_dict()

        try:
            resolved = await registry.resolve(req.service)
        except ServiceNotFound as exc:
            return GatewayResult.gateway_error("SERVICE_NOT_FOUND", str(exc)).to_dict()
        except RegistryUnavailable as exc:
            return GatewayResult.gateway_error("REGISTRY_UNAVAILABLE", str(exc)).to_dict()

        # Caller-supplied auth never wins over registry's; reserved headers stripped.
        merged_headers = _strip_reserved(req.headers)
        if resolved.auth_header_name:
            merged_headers[resolved.auth_header_name] = resolved.auth_header_value

        url = _build_url(resolved.base_url, req.endpoint)
        try:
            response = await upstream.send(
                method=req.method,
                url=url,
                headers=merged_headers,
                body=req.body,
                timeout_ms=req.timeout_ms,
            )
        except UpstreamTimeout as exc:
            return GatewayResult.gateway_error("UPSTREAM_TIMEOUT", str(exc)).to_dict()
        except ResponseTooLarge as exc:
            return GatewayResult.gateway_error("RESPONSE_TOO_LARGE", str(exc)).to_dict()
        except UpstreamError as exc:
            return GatewayResult.gateway_error("UPSTREAM_ERROR", str(exc)).to_dict()

        return GatewayResult(
            status_code=response.status_code,
            headers=response.headers,
            body=response.body,
        ).to_dict()

    return call_internal


def _make_call_external_handler(
    policy: ExternalUrlPolicy,
    upstream: UpstreamHttpClient,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def call_external(params: dict[str, Any]) -> dict[str, Any]:
        try:
            req = CallExternalParams.from_dict(params)
        except InvalidParams as exc:
            return GatewayResult.gateway_error("BAD_REQUEST", str(exc)).to_dict()

        try:
            policy.check(req.url)
        except SchemeNotAllowed as exc:
            return GatewayResult.gateway_error("SCHEME_NOT_ALLOWED", str(exc)).to_dict()
        except HostNotAllowed as exc:
            return GatewayResult.gateway_error("HOST_NOT_ALLOWED", str(exc)).to_dict()
        except PrivateIpBlocked as exc:
            return GatewayResult.gateway_error("PRIVATE_IP_BLOCKED", str(exc)).to_dict()

        # External calls do NOT have credentials injected by the gateway.
        # Caller may pass Authorization through `headers`; this is intentional.
        try:
            response = await upstream.send(
                method=req.method,
                url=req.url,
                headers=req.headers,
                body=req.body,
                timeout_ms=req.timeout_ms,
            )
        except UpstreamTimeout as exc:
            return GatewayResult.gateway_error("UPSTREAM_TIMEOUT", str(exc)).to_dict()
        except ResponseTooLarge as exc:
            return GatewayResult.gateway_error("RESPONSE_TOO_LARGE", str(exc)).to_dict()
        except UpstreamError as exc:
            return GatewayResult.gateway_error("UPSTREAM_ERROR", str(exc)).to_dict()

        return GatewayResult(
            status_code=response.status_code,
            headers=response.headers,
            body=response.body,
        ).to_dict()

    return call_external


def _make_invoke_app_function_handler(
    resolve_client: AppFunctionResolveClient,
    upstream: UpstreamHttpClient,
    allowed_schemes: tuple[str, ...] = ("https",),
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def invoke_app_function(params: dict[str, Any]) -> dict[str, Any]:
        try:
            req = InvokeAppFunctionParams.from_dict(params)
        except InvalidParams as exc:
            return GatewayResult.gateway_error("BAD_REQUEST", str(exc)).to_dict()

        try:
            resolved = (
                await resolve_client.resolve_by_name(req.app, req.function)
                if req.by_name
                else await resolve_client.resolve(req.function_id)
            )
        except ServiceNotFound as exc:
            return GatewayResult.gateway_error("FUNCTION_NOT_FOUND", str(exc)).to_dict()
        except RegistryUnavailable as exc:
            return GatewayResult.gateway_error("REGISTRY_UNAVAILABLE", str(exc)).to_dict()

        try:
            built = build_upstream_request(resolved, req)
        except InvalidParams as exc:
            return GatewayResult.gateway_error("BAD_REQUEST", str(exc)).to_dict()

        # External targets are SSRF-checked against the app's own allowlist.
        if resolved.kind == "external":
            policy = ExternalUrlPolicy(
                allowed_hosts=tuple(resolved.allowed_hosts),
                allowed_schemes=allowed_schemes,
            )
            try:
                policy.check(built.url)
            except SchemeNotAllowed as exc:
                return GatewayResult.gateway_error("SCHEME_NOT_ALLOWED", str(exc)).to_dict()
            except HostNotAllowed as exc:
                return GatewayResult.gateway_error("HOST_NOT_ALLOWED", str(exc)).to_dict()
            except PrivateIpBlocked as exc:
                return GatewayResult.gateway_error("PRIVATE_IP_BLOCKED", str(exc)).to_dict()

        # Caller override wins; else use the function's configured timeout
        # (resolved.timeout_ms); 0/None falls back to the client default.
        effective_timeout_ms = (
            req.timeout_ms if req.timeout_ms is not None else (resolved.timeout_ms or None)
        )
        try:
            response = await upstream.send(
                method=built.method,
                url=built.url,
                headers=built.headers,
                body=built.body,
                timeout_ms=effective_timeout_ms,
            )
        except UpstreamTimeout as exc:
            return GatewayResult.gateway_error("UPSTREAM_TIMEOUT", str(exc)).to_dict()
        except ResponseTooLarge as exc:
            return GatewayResult.gateway_error("RESPONSE_TOO_LARGE", str(exc)).to_dict()
        except UpstreamError as exc:
            return GatewayResult.gateway_error("UPSTREAM_ERROR", str(exc)).to_dict()

        return GatewayResult(
            status_code=response.status_code,
            headers=response.headers,
            body=response.body,
        ).to_dict()

    return invoke_app_function


_CALL_INTERNAL_INPUT_SCHEMA = [
    {"key": "service", "type": "string", "required": True, "label": "Registered service name"},
    {"key": "endpoint", "type": "string", "required": True, "label": "Endpoint path"},
    {"key": "method", "type": "string", "required": True, "label": "HTTP method", "default": "GET"},
    {"key": "headers", "type": "object", "required": False, "label": "Headers (registry auth injected automatically)"},
    {"key": "body", "type": "object", "required": False, "label": "Body"},
    {"key": "timeout_ms", "type": "number", "required": False, "label": "Timeout in ms (capped by invoke RPC 30s)"},
]

_CALL_EXTERNAL_INPUT_SCHEMA = [
    {"key": "url", "type": "string", "required": True, "label": "Full https URL"},
    {"key": "method", "type": "string", "required": True, "label": "HTTP method", "default": "GET"},
    {"key": "headers", "type": "object", "required": False, "label": "Headers (caller may include Authorization)"},
    {"key": "body", "type": "object", "required": False, "label": "Body"},
    {"key": "timeout_ms", "type": "number", "required": False, "label": "Timeout in ms (capped by invoke RPC 30s)"},
]

# Two ways to address the function: `function_id`, or (`app`, `function`). Neither is
# individually `required` because exactly one FORM is — a constraint the flat key/type
# schema cannot express, so from_dict enforces it and the labels say so.
_INVOKE_APP_FUNCTION_INPUT_SCHEMA = [
    {"key": "function_id", "type": "string", "required": False,
     "label": "Gateway function id (or use app + function)"},
    {"key": "app", "type": "string", "required": False,
     "label": "Gateway app name (with `function`; portable across environments)"},
    {"key": "function", "type": "string", "required": False,
     "label": "Gateway function name (with `app`)"},
    {"key": "args", "type": "object", "required": False, "label": "Args keyed by param (placed by location)"},
    {"key": "headers", "type": "object", "required": False, "label": "Extra headers (reserved auth stripped)"},
    {"key": "timeout_ms", "type": "number", "required": False, "label": "Timeout in ms"},
]

_OUTPUT_SCHEMA = [
    {"key": "status_code", "type": "number"},
    {"key": "headers", "type": "object"},
    {"key": "body", "type": "object"},
    {"key": "error", "type": "object", "description": "Set when the gateway rejected the call; status_code/headers/body are unset."},
]


def build_gateway_worker_functions(
    *,
    control_plane_url: str,
    resolve_cache_ttl_s: float,
    upstream_default_timeout_s: float,
    upstream_max_response_bytes: int,
    external_allowlist_path: str,
    invoke_allowed_schemes: tuple[str, ...] = ("https",),
) -> list[WorkerFunction]:
    """Wire deps + return the three WorkerFunctions registered with the SDK."""
    registry = InternalServiceRegistryClient(
        control_plane_url=control_plane_url,
        ttl_s=resolve_cache_ttl_s,
    )
    app_fn_resolver = AppFunctionResolveClient(
        control_plane_url=control_plane_url,
        ttl_s=resolve_cache_ttl_s,
    )
    upstream = UpstreamHttpClient(
        default_timeout_s=upstream_default_timeout_s,
        max_response_bytes=upstream_max_response_bytes,
    )
    policy = load_external_url_policy(external_allowlist_path)

    return [
        WorkerFunction(
            name="call_internal",
            description=(
                "Forward an HTTP request to a registered internal service. "
                "Auth header is injected by the gateway-worker from the registry; "
                "caller-supplied Authorization/Cookie headers are stripped."
            ),
            input_schema=_CALL_INTERNAL_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            handler=_make_call_internal_handler(registry, upstream),
        ),
        WorkerFunction(
            name="call_external",
            description=(
                "Forward an HTTP request to an allow-listed external URL. "
                "No credentials injected; caller may supply Authorization in headers. "
                "Host must match the allowlist; https-only; private IPs blocked."
            ),
            input_schema=_CALL_EXTERNAL_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            handler=_make_call_external_handler(policy, upstream),
        ),
        WorkerFunction(
            name="invoke_app_function",
            description=(
                "Invoke a registered gateway function, addressed either by `function_id` "
                "or by (`app`, `function`) name — the name pair is portable across "
                "environments, the id is not. The gateway-worker resolves the function "
                "(method/path/params/base_url|allowlist/auth) from the control plane, "
                "builds the request from args, enforces the per-app allowlist for "
                "external targets, and proxies it."
            ),
            input_schema=_INVOKE_APP_FUNCTION_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            handler=_make_invoke_app_function_handler(
                app_fn_resolver, upstream, allowed_schemes=invoke_allowed_schemes,
            ),
        ),
    ]
