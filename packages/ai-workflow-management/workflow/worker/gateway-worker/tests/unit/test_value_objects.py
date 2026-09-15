"""Unit tests for CallInternalParams, CallExternalParams, GatewayResult."""
from __future__ import annotations

import pytest

from app.layer1_domain.value_objects.gateway_request import (
    CallExternalParams,
    CallInternalParams,
    GatewayResult,
    InvalidParams,
)


# ── CallInternalParams ────────────────────────────────────────────────


@pytest.mark.unit
def test_call_internal_happy_path():
    p = CallInternalParams.from_dict({
        "service": "governance",
        "endpoint": "/tasks/123",
        "method": "post",
        "headers": {"X-Trace": "abc"},
        "body": {"x": 1},
        "timeout_ms": 5000,
    })
    assert p.service == "governance"
    assert p.endpoint == "/tasks/123"
    assert p.method == "POST"
    assert p.headers == {"X-Trace": "abc"}
    assert p.timeout_ms == 5000


@pytest.mark.unit
def test_call_internal_defaults_method_to_get():
    p = CallInternalParams.from_dict({"service": "x", "endpoint": "/y"})
    assert p.method == "GET"


@pytest.mark.unit
def test_call_internal_rejects_missing_service():
    with pytest.raises(InvalidParams, match="service"):
        CallInternalParams.from_dict({"endpoint": "/y"})


@pytest.mark.unit
def test_call_internal_rejects_unknown_method():
    with pytest.raises(InvalidParams, match="method"):
        CallInternalParams.from_dict({"service": "x", "endpoint": "/y", "method": "TRACE"})


@pytest.mark.unit
def test_call_internal_rejects_negative_timeout():
    with pytest.raises(InvalidParams, match="timeout_ms"):
        CallInternalParams.from_dict(
            {"service": "x", "endpoint": "/y", "timeout_ms": -1}
        )


# ── CallExternalParams ────────────────────────────────────────────────


@pytest.mark.unit
def test_call_external_happy_path():
    p = CallExternalParams.from_dict({
        "url": "https://api.partner.com/x",
        "method": "GET",
    })
    assert p.url == "https://api.partner.com/x"
    assert p.method == "GET"


@pytest.mark.unit
def test_call_external_rejects_non_absolute_url():
    with pytest.raises(InvalidParams, match="absolute"):
        CallExternalParams.from_dict({"url": "/relative/path"})


@pytest.mark.unit
def test_call_external_rejects_missing_url():
    with pytest.raises(InvalidParams, match="url"):
        CallExternalParams.from_dict({})


# ── GatewayResult ─────────────────────────────────────────────────────


@pytest.mark.unit
def test_gateway_result_to_dict_for_success():
    r = GatewayResult(status_code=200, headers={"x": "y"}, body={"ok": True})
    assert r.to_dict() == {"status_code": 200, "headers": {"x": "y"}, "body": {"ok": True}}


@pytest.mark.unit
def test_gateway_result_to_dict_for_error_drops_other_fields():
    r = GatewayResult.gateway_error("HOST_NOT_ALLOWED", "x")
    assert r.to_dict() == {"error": {"code": "HOST_NOT_ALLOWED", "message": "x"}}
