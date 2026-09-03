"""SA-1905 C2 — /execute wiring: a large output field becomes a file_ref.

End-to-end through the FastAPI route (multi-type path) with a fake uploader:
when streaming is enabled, a large list-of-dicts output field is converted +
uploaded at the worker and replaced by a file_ref; scalars are untouched;
disabled -> inline passthrough.

Skipped when the native json_csv_streamer wheel isn't installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("json_csv_streamer")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router
from worker_sdk.layer4_frameworks.providers.data_io.streaming_output_converter import (
    StreamingOutputConverter,
)


class _FakeUploader:
    async def upload(self, file_bytes, filename, content_type="text/csv", idempotency_key=None):
        return "worker-file-1"


class _NT:
    def __init__(self, handler):
        self.handler = handler


def _big(n):
    return [{"id": i, "sku": f"S{i}", "v": i % 5} for i in range(n)]


def _client(handler, converter):
    container = {"_dependencies": {
        "node_type_registry": {"mapper": _NT(handler)},
        "output_converter": converter,
    }}
    app = FastAPI()
    app.include_router(create_router(container), prefix="/api/v1")
    return TestClient(app)


def _payload():
    return {"task_id": "t9", "action": "execute", "worker_type": "mapper",
            "inputs": {}, "parameters": {}}


def test_execute_streams_large_output_field_to_file_ref():
    async def handler(resolved, params):
        return {"rows": _big(200), "count": 200}

    conv = StreamingOutputConverter(_FakeUploader(), enabled=True, threshold_bytes=1)
    resp = _client(handler, conv).post("/api/v1/execute", json=_payload())

    assert resp.status_code == 200
    out = resp.json()["outputs"]
    assert out["count"] == 200                      # scalar unchanged
    assert out["rows"].get("__file_ref") is True    # large list -> file_ref
    assert out["rows"]["file_id"] == "worker-file-1"
    assert out["rows"]["row_count"] == 200
    assert "__row_id" not in out["rows"]["columns"]


def test_execute_disabled_keeps_output_inline():
    async def handler(resolved, params):
        return {"rows": _big(200)}

    conv = StreamingOutputConverter(_FakeUploader(), enabled=False, threshold_bytes=1)
    resp = _client(handler, conv).post("/api/v1/execute", json=_payload())

    out = resp.json()["outputs"]
    assert isinstance(out["rows"], list) and len(out["rows"]) == 200  # unchanged


def test_execute_no_converter_is_passthrough():
    async def handler(resolved, params):
        return {"rows": _big(200)}

    container = {"_dependencies": {"node_type_registry": {"mapper": _NT(handler)}}}
    app = FastAPI()
    app.include_router(create_router(container), prefix="/api/v1")
    resp = TestClient(app).post("/api/v1/execute", json=_payload())

    out = resp.json()["outputs"]
    assert isinstance(out["rows"], list) and len(out["rows"]) == 200
