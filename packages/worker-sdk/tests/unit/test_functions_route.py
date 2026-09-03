"""Regression: the worker-function HTTP routes must mount when the
``function_registry`` is provided.

``function_registry`` is an infrastructure dependency, so ``build_app_container``
exposes it under ``container["_dependencies"]`` (alongside ``file_ref_resolver``
and ``node_type_registry``) — NOT as a top-level container key (those are the
auto-discovered features). The functions block in ``create_router`` previously
checked the top-level ``container``, so the check was always False and
``GET /functions`` / ``POST /functions/{name}/invoke`` never mounted for ANY
worker. A function-only worker (the gateway-worker) was therefore entirely
non-invokable — only ``/health`` + ``/ready`` were exposed.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router
from worker_sdk.layer2_application.services.function_registry import FunctionRegistry
from worker_sdk.layer1_domain.entities.worker_function import WorkerFunction


def _registry_with_echo() -> FunctionRegistry:
    reg = FunctionRegistry()

    async def echo(params):
        return {"echoed": params}

    reg.register(WorkerFunction(name="echo", description="echoes its params", handler=echo))
    return reg


def _client(container: dict) -> TestClient:
    app = FastAPI()
    app.include_router(create_router(container), prefix="/api/v1")
    return TestClient(app)


def test_functions_routes_mount_when_registry_in_dependencies():
    # function_registry lives under _dependencies (where bootstrap puts it).
    client = _client({"_dependencies": {"function_registry": _registry_with_echo()}})

    # GET /functions lists the registered function (route is mounted).
    r = client.get("/api/v1/functions")
    assert r.status_code == 200
    assert [f["name"] for f in r.json()] == ["echo"]

    # POST .../invoke runs the handler and returns its result.
    r = client.post("/api/v1/functions/echo/invoke", json={"params": {"x": 1}})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert body["result"] == {"echoed": {"x": 1}}


def test_functions_routes_absent_without_registry():
    """No function_registry → routes stay unmounted (404). Unchanged behaviour."""
    client = _client({"_dependencies": {}})
    assert client.get("/api/v1/functions").status_code == 404
    assert client.post("/api/v1/functions/echo/invoke", json={}).status_code == 404
