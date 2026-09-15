"""Unit tests for the OpenAPI -> gateway function signature parser."""
from __future__ import annotations

from app.layer1_domain.value_objects.gateway import AppKind, parse_openapi
from app.layer1_domain.entities.gateway_function import GatewayFunction
from app.layer1_domain.value_objects.gateway import FunctionParam, HttpMethod, PathTemplate

import pytest


_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.partner.com/v1"}],
    "paths": {
        "/orders/{order_id}": {
            "get": {
                "operationId": "getOrder",
                "parameters": [
                    {"name": "order_id", "in": "path", "required": True, "schema": {"type": "integer"}},
                    {"name": "expand", "in": "query", "schema": {"type": "string"}},
                    {"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                    {"name": "sid", "in": "cookie", "schema": {"type": "string"}},  # unsupported -> dropped
                ],
                "responses": {"200": {"content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}, "total": {"type": "number"}},
                }}}}},
            },
            "post": {  # no operationId + undeclared path param -> name fallback + synth
                "summary": "Create",
                "requestBody": {"required": True, "content": {"application/json": {
                    "schema": {"$ref": "#/components/schemas/Order"}}}},
                "responses": {"201": {"description": "created"}},
            },
        },
    },
    "components": {"schemas": {"Order": {"type": "object", "properties": {"sku": {"type": "string"}}}}},
}


def test_internal_relative_path_and_param_mapping():
    sigs = {s.name: s for s in parse_openapi(_SPEC, kind=AppKind.INTERNAL)}
    assert set(sigs) == {"getOrder", "post_orders_order_id"}
    get = sigs["getOrder"]
    assert get.method == "GET"
    assert get.path_template == "/orders/{order_id}"   # internal stays relative
    by_key = {p["key"]: p for p in get.input_params}
    assert by_key["order_id"]["location"] == "path"
    assert by_key["order_id"]["type"] == "number"       # integer -> number
    assert by_key["expand"]["location"] == "query"
    assert by_key["X-Trace"]["location"] == "header"
    assert "sid" not in by_key                           # cookie dropped
    assert {o["key"] for o in get.output_fields} == {"id", "total"}


def test_external_absolute_url_from_server():
    sigs = {s.name: s for s in parse_openapi(_SPEC, kind=AppKind.EXTERNAL)}
    assert sigs["getOrder"].path_template == "https://api.partner.com/v1/orders/{order_id}"


def test_missing_path_param_is_synthesized_and_body_added():
    post = next(s for s in parse_openapi(_SPEC, kind=AppKind.INTERNAL) if s.name == "post_orders_order_id")
    locs = {p["key"]: p["location"] for p in post.input_params}
    assert locs["order_id"] == "path"   # synthesized (not declared in spec)
    assert locs["body"] == "body"


def test_parsed_signatures_satisfy_entity_invariant():
    """Every parsed function must build a valid GatewayFunction (path<->param match)."""
    for s in parse_openapi(_SPEC, kind=AppKind.INTERNAL):
        GatewayFunction.create(
            app_id="a1",
            name=s.name,
            method=HttpMethod.coerce(s.method),
            path_template=PathTemplate(s.path_template),
            input_params=[FunctionParam.from_dict(p) for p in s.input_params],
            created_by="t",
        )  # raises ValidationError if the signature is inconsistent


def test_operation_param_overrides_shared_path_item_param():
    spec = {"openapi": "3.0.0", "paths": {"/a": {
        "parameters": [{"name": "q", "in": "query", "required": False, "schema": {"type": "string"}}],
        "get": {
            "operationId": "g",
            "parameters": [{"name": "q", "in": "query", "required": True, "schema": {"type": "integer"}}],
            "responses": {"200": {"description": "ok"}},
        },
    }}}
    sig = parse_openapi(spec, kind=AppKind.INTERNAL)[0]
    q = next(p for p in sig.input_params if p["key"] == "q")
    assert q["required"] is True and q["type"] == "number"   # op-level (integer→number) won


def test_placeholder_param_misdeclared_as_query_is_forced_to_path():
    spec = {"openapi": "3.0.0", "paths": {"/a/{id}": {"get": {
        "operationId": "g",
        "parameters": [{"name": "id", "in": "query", "schema": {"type": "string"}}],  # wrong 'in'
        "responses": {"200": {"description": "ok"}},
    }}}}
    sig = parse_openapi(spec, kind=AppKind.INTERNAL)[0]
    locs = {p["key"]: p["location"] for p in sig.input_params}
    assert locs == {"id": "path"}   # forced to path; no duplicate 'id'
    GatewayFunction.create(  # still satisfies the entity invariant
        app_id="a1", name=sig.name, method=HttpMethod.coerce(sig.method),
        path_template=PathTemplate(sig.path_template),
        input_params=[FunctionParam.from_dict(p) for p in sig.input_params], created_by="t",
    )


def test_rejects_non_openapi():
    with pytest.raises(ValueError):
        parse_openapi({"no": "paths"}, kind=AppKind.INTERNAL)
