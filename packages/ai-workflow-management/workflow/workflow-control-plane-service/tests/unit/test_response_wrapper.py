"""Regression check for ResponseWrapperMiddleware's already-wrapped detection.

The bug: a node-definition *test* result is a domain payload that legitimately
carries status="error" (worker returned TaskStatus.ERROR) plus an "error" key.
The old check treated that as an already-wrapped envelope and returned it flat,
so the frontend's `data.data` was undefined and no result rendered.
"""
import asyncio
import json

from starlette.responses import StreamingResponse

from app.layer3_adapters.controllers.restful.v1.middleware.response_wrapper import (
    ResponseWrapperMiddleware,
)


def _wrap(body: dict) -> dict:
    mw = ResponseWrapperMiddleware(app=None)

    async def call_next(_request):
        async def stream():
            yield json.dumps(body).encode()
        return StreamingResponse(stream(), media_type="application/json")

    request = type("R", (), {"url": type("U", (), {"path": "/api/v1/x"})()})()
    resp = asyncio.run(mw.dispatch(request, call_next))
    return json.loads(bytes(resp.body))


def test_domain_payload_with_error_status_is_wrapped():
    # Mirrors a failed worker test result (many keys + status/error).
    body = {"node_definition_id": "n1", "status": "error", "resolved_input": {}, "error": ""}
    out = _wrap(body)
    assert out["status"] == "ok"
    assert out["data"] == body


def test_genuine_error_envelope_is_not_double_wrapped():
    body = {"status": "error", "error": {"code": "X", "message": "boom"}}
    assert _wrap(body) == body


def test_genuine_success_envelope_is_not_double_wrapped():
    body = {"status": "ok", "data": {"a": 1}}
    assert _wrap(body) == body


if __name__ == "__main__":
    test_domain_payload_with_error_status_is_wrapped()
    test_genuine_error_envelope_is_not_double_wrapped()
    test_genuine_success_envelope_is_not_double_wrapped()
    print("ok")
