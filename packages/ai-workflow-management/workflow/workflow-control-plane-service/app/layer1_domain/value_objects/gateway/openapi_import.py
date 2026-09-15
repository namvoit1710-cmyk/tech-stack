"""Parse an OpenAPI v3 document into gateway function signatures (producer side).

``parse_openapi(spec, kind, server_url)`` walks ``paths`` -> operations and returns
one ``FunctionSignature`` per operation, shaped to exactly what the create/update
gateway-function use cases consume. Pure + deterministic, so it is unit-testable
and the sync use case stays orchestration-only.

Scope (ponytail — extend when a real spec needs it):
- OpenAPI **v3** only (``paths`` + ``components`` + ``servers``). Swagger v2 is a follow-up.
- Local ``#/components/...`` ``$ref``s are resolved (cycle-guarded); remote/file refs -> ``object``.
- ``requestBody`` -> a single ``body`` object param (not exploded per-field).
- Path params are reconciled against the path's ``{placeholders}`` so the result always
  satisfies ``GatewayFunction._check_signature`` (every placeholder has a path param and
  vice-versa). Declared path params not in the path are dropped; missing ones synthesized.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.value_objects.gateway.app_kind import AppKind

# HTTP verbs OpenAPI puts under a path item (others — parameters/summary/$ref — are skipped).
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
# Param locations we map; "cookie" is intentionally unsupported.
_NON_PATH_LOCATIONS = ("query", "header")
# Registry-accepted types (mirrors FunctionParam.VALID_PARAM_TYPES).
_VALID_TYPES = frozenset({"string", "number", "boolean", "object", "array"})
# Same placeholder grammar PathTemplate enforces.
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class FunctionSignature:
    """A function's importable shape — the fields the create/update UCs take."""

    name: str
    method: str
    path_template: str
    input_params: list[dict[str, Any]] = field(default_factory=list)
    output_fields: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""


def _norm_type(t: Any) -> str:
    t = str(t or "").lower()
    if t == "integer":
        return "number"
    return t if t in _VALID_TYPES else "string"


#: Leaves room for the ``_2``/``_37`` de-dupe suffix appended below to stay
#: inside ``resource_name.MAX_NAME_LENGTH`` (the NVARCHAR(128) NAME column).
#: An operationId longer than this used to slug straight through every check
#: and fail at INSERT with a 500.
_SLUG_BUDGET = 120


def _slug(text: str) -> str:
    """Fold arbitrary text into a gateway-safe function name.

    Everything non-alphanumeric collapses to ``_``, so the output is always
    inside ``resource_name``'s character set by construction — an OpenAPI sync
    can never mint a name the registry would refuse, or one that cannot be
    addressed in a URL path afterwards.
    """
    out = "".join(c if c.isalnum() else "_" for c in str(text)).strip("_")
    while "__" in out:
        out = out.replace("__", "_")
    # Trim after collapsing, then re-strip: the cut can land on a separator.
    out = out[:_SLUG_BUDGET].strip("_")
    return out or "op"


def _resolve_ref(spec: dict, node: Any, _seen: tuple = ()) -> Any:
    """Resolve a local ``#/...`` ``$ref`` (cycle-guarded). Remote/file refs -> ``{}``."""
    if not isinstance(node, dict) or "$ref" not in node:
        return node
    ref = node.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/") or ref in _seen:
        return {}
    target: Any = spec
    for part in ref[2:].split("/"):
        if not isinstance(target, dict):
            return {}
        target = target.get(part, {})
    return _resolve_ref(spec, target, _seen + (ref,))


def _schema_type(spec: dict, schema: Any) -> str:
    schema = _resolve_ref(spec, schema)
    return _norm_type(schema.get("type")) if isinstance(schema, dict) else "string"


def _params(spec: dict, op: dict, shared: list, path_template: str) -> list[dict[str, Any]]:
    placeholders = set(_PLACEHOLDER_RE.findall(path_template))
    # Dedup by param key — the entity requires unique input-param keys regardless of
    # location. Operation-level params override shared path-item params of the same name
    # (OpenAPI precedence); dict keeps first-seen order while overwriting the value.
    collected: dict[str, dict[str, Any]] = {}
    for raw in list(shared) + list(op.get("parameters") or []):
        p = _resolve_ref(spec, raw)
        if not isinstance(p, dict):
            continue
        loc = str(p.get("in") or "")
        key = str(p.get("name") or "")
        if not key:
            continue
        if key in placeholders:
            loc = "path"                  # a placeholder is authoritatively a path param
        elif loc not in _NON_PATH_LOCATIONS:
            continue                      # orphan path param / cookie / unknown → drop
        collected[key] = {
            "key": key,
            "type": _schema_type(spec, p.get("schema") or {}),
            "location": loc,
            "required": bool(p.get("required", loc == "path")),
            "label": key,
        }
    out = list(collected.values())
    # Synthesize a path param for any placeholder the spec didn't declare.
    for ph in placeholders:
        if ph not in collected:
            out.append({"key": ph, "type": "string", "location": "path", "required": True, "label": ph})
    # Body -> one object param.
    body = _resolve_ref(spec, op.get("requestBody") or {})
    if isinstance(body, dict) and body.get("content") and "body" not in collected:
        out.append({
            "key": "body",
            "type": "object",
            "location": "body",
            "required": bool(body.get("required", False)),
            "label": "body",
        })
    return out


def _outputs(spec: dict, op: dict) -> list[dict[str, Any]]:
    responses = op.get("responses") or {}
    if not isinstance(responses, dict):
        return []
    chosen = responses.get("200") or responses.get("201")
    if chosen is None:
        for code, val in responses.items():
            if str(code).startswith("2"):
                chosen = val
                break
    chosen = _resolve_ref(spec, chosen or {})
    if not isinstance(chosen, dict):
        return []
    content = (chosen.get("content") or {}).get("application/json") or {}
    schema = _resolve_ref(spec, content.get("schema") or {})
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return []
    return [
        {"key": str(key), "type": _schema_type(spec, sub), "label": str(key)}
        for key in props
        for sub in [props[key]]
    ]


def _default_server(spec: dict) -> str:
    servers = spec.get("servers")
    if isinstance(servers, list) and servers and isinstance(servers[0], dict):
        return str(servers[0].get("url") or "")
    return ""


def parse_openapi(
    spec: dict, *, kind: AppKind, server_url: str = ""
) -> list[FunctionSignature]:
    """Map an OpenAPI v3 document to function signatures.

    Internal apps get a relative ``path_template`` (joined onto the app's base_url at
    runtime); external apps get an absolute ``server_url + path`` URL. Raises
    ``ValueError`` if the document is not a usable OpenAPI object.
    """
    if not isinstance(spec, dict):
        raise ValueError("OpenAPI spec must be a JSON object")
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI spec has no 'paths' object")

    base = (server_url or _default_server(spec)).rstrip("/")
    sigs: list[FunctionSignature] = []
    seen_names: set[str] = set()
    for path, raw_item in paths.items():
        item = _resolve_ref(spec, raw_item)
        if not isinstance(item, dict):
            continue
        shared = item.get("parameters") or []
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            op_id = str(op.get("operationId") or "").strip()
            name = _slug(op_id) if op_id else _slug(f"{method}_{path}")
            base_name, i = name, 2
            while name in seen_names:   # de-dupe collisions within one spec
                name = f"{base_name}_{i}"
                i += 1
            seen_names.add(name)

            path_template = (f"{base}{path}" if base else path) if kind is AppKind.EXTERNAL else path
            sigs.append(FunctionSignature(
                name=name,
                method=method.upper(),
                path_template=path_template,
                input_params=_params(spec, op, shared, path_template),
                output_fields=_outputs(spec, op),
                description=str(op.get("summary") or op.get("description") or "")[:512],
            ))
    return sigs


if __name__ == "__main__":
    # ponytail self-check: parse covers params, $ref body, 200 schema, id fallback, path synth.
    _spec = {
        "openapi": "3.0.0",
        "servers": [{"url": "https://api.partner.com"}],
        "paths": {
            "/orders/{order_id}": {
                "get": {
                    "operationId": "getOrder",
                    "parameters": [
                        {"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                        {"name": "expand", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"content": {"application/json": {"schema": {
                        "type": "object", "properties": {"id": {"type": "integer"}, "total": {"type": "number"}}}}}}},
                },
                "post": {  # no operationId, undeclared path param -> name fallback + synth
                    "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Order"}}}},
                    "responses": {"201": {"description": "created"}},
                },
            }
        },
        "components": {"schemas": {"Order": {"type": "object", "properties": {"sku": {"type": "string"}}}}},
    }
    internal = parse_openapi(_spec, kind=AppKind.INTERNAL)
    assert {s.name for s in internal} == {"getOrder", "post_orders_order_id"}, [s.name for s in internal]
    get = next(s for s in internal if s.name == "getOrder")
    assert get.method == "GET" and get.path_template == "/orders/{order_id}"
    assert {p["key"]: p["type"] for p in get.input_params} == {"order_id": "number", "expand": "string"}
    assert {p["key"]: p["location"] for p in get.input_params}["order_id"] == "path"
    assert {o["key"] for o in get.output_fields} == {"id", "total"}
    post = next(s for s in internal if s.name != "getOrder")
    # body param present; missing path param 'order_id' synthesized so the signature is valid
    locs = {p["key"]: p["location"] for p in post.input_params}
    assert locs.get("order_id") == "path" and locs.get("body") == "body", post.input_params
    external = parse_openapi(_spec, kind=AppKind.EXTERNAL)
    eget = next(s for s in external if s.name == "getOrder")
    assert eget.path_template == "https://api.partner.com/orders/{order_id}", eget.path_template
    print("openapi_import self-check OK")
