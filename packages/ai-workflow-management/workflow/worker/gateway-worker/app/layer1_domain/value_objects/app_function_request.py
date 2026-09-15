"""Build a concrete upstream request from a resolved call-spec + caller args.

Worker-domain logic for `invoke_app_function`: fills the path template from
`path`-located args, places `query`/`header`/`body` args by location, joins the
URL (internal: base_url + path; external: path_template is absolute), and injects
the resolved auth header (stripping caller-supplied reserved headers first).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from app.layer1_domain.value_objects.gateway_request import InvalidParams
from app.layer4_frameworks.registry_client import ResolvedAppFunction

_RESERVED_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})


def _clean(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidParams(f"`{key}` must be a string")
    return value.strip()


@dataclass(frozen=True)
class InvokeAppFunctionParams:
    """Selects a gateway function by id, or by (app, function) name.

    Ids are minted per-environment, so a node that pins one cannot be promoted
    dev → qa → prod unchanged. The name pair is stable, and a function name is
    unique within its app. Exactly one form is accepted: passing both is an error
    rather than a silent precedence rule, because the two could disagree and the
    caller would never learn which one actually ran.
    """
    function_id: str
    app: str
    function: str
    args: dict[str, Any]
    headers: dict[str, str]
    timeout_ms: int | None

    @property
    def by_name(self) -> bool:
        return not self.function_id

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "InvokeAppFunctionParams":
        function_id = _clean(raw, "function_id")
        app = _clean(raw, "app")
        function = _clean(raw, "function")

        if function_id and (app or function):
            raise InvalidParams(
                "pass either `function_id` or (`app`, `function`) — not both"
            )
        if not function_id:
            if not (app and function):
                raise InvalidParams(
                    "`function_id` is required, or both `app` and `function`"
                )

        args = raw.get("args") or {}
        if not isinstance(args, dict):
            raise InvalidParams("`args` must be an object")
        headers = raw.get("headers") or {}
        if not isinstance(headers, dict):
            raise InvalidParams("`headers` must be an object")
        timeout_ms = raw.get("timeout_ms")
        if timeout_ms is not None and (not isinstance(timeout_ms, (int, float)) or timeout_ms <= 0):
            raise InvalidParams("`timeout_ms` must be a positive number when provided")
        return cls(
            function_id=function_id,
            app=app,
            function=function,
            args=dict(args),
            headers={str(k): str(v) for k, v in headers.items()},
            timeout_ms=int(timeout_ms) if timeout_ms is not None else None,
        )


@dataclass(frozen=True)
class BuiltRequest:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None


def _join(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def build_upstream_request(
    resolved: ResolvedAppFunction, params: InvokeAppFunctionParams
) -> BuiltRequest:
    by_loc: dict[str, list[dict]] = {}
    for p in resolved.params:
        by_loc.setdefault(str(p.get("location", "query")), []).append(p)
    args = params.args

    def _require(p: dict) -> None:
        if p.get("required") and p["key"] not in args:
            raise InvalidParams(f"missing required '{p.get('location')}' param '{p['key']}'")

    # Path
    path = resolved.path_template
    for p in by_loc.get("path", []):
        _require(p)
        if p["key"] in args:
            path = path.replace("{" + p["key"] + "}", str(args[p["key"]]))
    if "{" in path or "}" in path:
        raise InvalidParams(f"unfilled path placeholders in path_template {path!r}")

    # Query
    query: dict[str, Any] = {}
    for p in by_loc.get("query", []):
        _require(p)
        if p["key"] in args:
            query[p["key"]] = args[p["key"]]

    # Headers (strip caller reserved, then header-located args, then injected auth)
    headers = {k: v for k, v in params.headers.items() if k.lower() not in _RESERVED_HEADERS}
    for p in by_loc.get("header", []):
        _require(p)
        if p["key"] in args:
            headers[p["key"]] = str(args[p["key"]])

    # Body: single body param passes through; multiple → dict keyed by param.
    body_params = by_loc.get("body", [])
    body: Any = None
    if len(body_params) == 1:
        _require(body_params[0])
        if body_params[0]["key"] in args:
            body = args[body_params[0]["key"]]
    elif body_params:
        collected: dict[str, Any] = {}
        for p in body_params:
            _require(p)
            if p["key"] in args:
                collected[p["key"]] = args[p["key"]]
        body = collected or None

    url = path if resolved.kind == "external" else _join(resolved.base_url, path)
    if query:
        url = url + ("&" if "?" in url else "?") + urlencode(query, doseq=True)

    if resolved.auth_header_name:
        headers[resolved.auth_header_name] = resolved.auth_header_value

    return BuiltRequest(method=resolved.method, url=url, headers=headers, body=body)
