"""Wiring for the result budget: shadow by default, enforce behind a flag.

Shadow mode must change NOTHING about the outcome — that is what makes it safe
to ship alongside the fix it is measuring. Enforce mode must fail the task with
the attribution message, and on the ASYNC path it must post an error callback
rather than raise: a raise in that background task kills it with no callback and
the node hangs RUNNING forever (SA-1951, and the reason
``_maybe_stream_outputs`` documents itself as never-raising).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from worker_sdk.layer1_domain.value_objects.task_status import TaskStatus
from worker_sdk.layer3_adapters.controllers.restful.v1.routes import create_router


@dataclass
class _Out:
    task_id: str = "t1"
    status: TaskStatus = TaskStatus.SUCCESS
    outputs: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    duration_ms: float = 1.0
    output_reference: str = ""


class _UseCase:
    def __init__(self, outputs):
        self._outputs = outputs

    def execute(self, cmd):
        return _Out(task_id=cmd.task_id, outputs=dict(self._outputs))


class _NT:
    def __init__(self, handler):
        self.handler = handler


def _client(container):
    app = FastAPI()
    app.include_router(create_router(container), prefix="/api/v1")
    return TestClient(app)


# The shape that failed live: one huge str field that streaming cannot touch.
_OVERSIZED = {"resolved_body": "x" * 40_000, "status_code": 200}


@pytest.fixture
def small_budget():
    with patch(
        "worker_sdk.layer3_adapters.controllers.restful.v1.routes.settings"
    ) as s:
        s.RESULT_MAX_OUTPUT_BYTES = 4096
        s.RESULT_BUDGET_ENFORCE = False
        s.WORKER_CHUNKED_OUTPUT_ENABLED = False
        s.FILE_SERVICE_URL = ""
        yield s


def test_shadow_mode_changes_nothing(small_budget):
    """Default rollout state: measure and report, never alter the outcome."""
    client = _client({"execute_task_usecase": _UseCase(_OVERSIZED)})

    r = client.post("/api/v1/execute", json={
        "task_id": "shadow1", "action": "execute", "inputs": {}, "parameters": {},
    })

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == TaskStatus.SUCCESS.value
    assert body["outputs"]["resolved_body"] == _OVERSIZED["resolved_body"]


def test_enforce_mode_fails_the_task_with_attribution(small_budget):
    small_budget.RESULT_BUDGET_ENFORCE = True
    client = _client({"execute_task_usecase": _UseCase(_OVERSIZED)})

    r = client.post("/api/v1/execute", json={
        "task_id": "enforce1", "action": "execute", "inputs": {}, "parameters": {},
    })

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == TaskStatus.ERROR.value
    assert "resolved_body" in (body["error"] or "")
    assert "NOT-STREAMABLE" in (body["error"] or "")
    # The payload is what could not be delivered - do not ship it anyway.
    assert not body["outputs"]


def test_enforce_mode_on_the_multi_type_path_too(small_budget):
    small_budget.RESULT_BUDGET_ENFORCE = True

    def handler(inputs, params):
        return dict(_OVERSIZED)

    client = _client({"_dependencies": {"node_type_registry": {"m": _NT(handler)}}})

    r = client.post("/api/v1/execute", json={
        "task_id": "enforce2", "action": "execute", "worker_type": "m",
        "inputs": {}, "parameters": {},
    })

    body = r.json()
    assert body["status"] == TaskStatus.ERROR.value
    assert "resolved_body" in (body["error"] or "")


def test_enforce_mode_on_the_async_path_posts_an_error_callback(small_budget):
    """It must CALL BACK, not raise - a raise here hangs the node forever."""
    small_budget.RESULT_BUDGET_ENFORCE = True
    posted = {}

    def _capture(url, payload, task_id):
        posted.update(payload)
        return True

    with patch(
        "worker_sdk.layer3_adapters.controllers.restful.v1.routes._post_callback",
        _capture,
    ):
        client = _client({"execute_task_usecase": _UseCase(_OVERSIZED)})
        r = client.post("/api/v1/execute-async", json={
            "task_id": "enforce3", "action": "execute", "inputs": {},
            "parameters": {}, "callback_url": "http://executor/cb",
        })

    assert r.status_code == 202
    assert posted, "the background task must post a callback, never vanish"
    assert posted["status"] == TaskStatus.ERROR.value
    assert "resolved_body" in (posted["error"] or "")
    assert not posted["outputs"]


def test_a_result_within_budget_is_untouched_even_when_enforcing(small_budget):
    small_budget.RESULT_BUDGET_ENFORCE = True
    client = _client({"execute_task_usecase": _UseCase({"ok": True})})

    r = client.post("/api/v1/execute", json={
        "task_id": "fine1", "action": "execute", "inputs": {}, "parameters": {},
    })

    body = r.json()
    assert body["status"] == TaskStatus.SUCCESS.value
    assert body["outputs"] == {"ok": True}
