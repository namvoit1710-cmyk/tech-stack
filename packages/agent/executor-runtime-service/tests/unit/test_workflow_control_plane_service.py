from __future__ import annotations

import httpx
import pytest

from app.layer4_frameworks.clients.workflow_control_plane.models import (
    GetDataRequest,
    PatchDataRequest,
    StartRunRequest,
    StopRunRequest,
    SubmitRequest,
)
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneError,
    WorkflowControlPlaneClient,
)


@pytest.mark.asyncio
async def test_start_run_posts_expected_payload():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://workflow.local/api/v1/runs"
        assert (
            request.read()
            == b'{"workflow_id":"wf-1","input_data":{"a":1},"predefined_run_id":"run-9","execution_mode":"sync","runtime_credentials":{"default":{"type":"bearer","config":{"token":"token-1"}}}}'
        )
        return httpx.Response(200, json={"status": "ok", "data": {"run_id": "run-9"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    result = await service.start_run(
        StartRunRequest(
            workflow_id="wf-1",
            predefined_run_id="run-9",
            input_data={"a": 1},
            execution_mode="sync",
            runtime_credentials={
                "default": {"type": "bearer", "config": {"token": "token-1"}}
            },
        )
    )

    assert result.id is None
    assert result.run_id == "run-9"
    assert result.to_tool_result()["id"] is None


@pytest.mark.asyncio
async def test_get_data_returns_response_model_and_etag():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert (
            str(request.url)
            == "http://workflow.local/api/v1/runs/run-1/nodes/node-1/data?side=input&task_id=task-1"
        )
        return httpx.Response(
            200, headers={"ETag": "etag-1"}, json={"status": "ok", "data": {"value": 1}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    result = await service.get_data(
        GetDataRequest(
            run_id="run-1",
            node_id="node-1",
            task_id="task-1",
        )
    )

    assert result.data == {"value": 1}
    assert result.etag == "etag-1"


@pytest.mark.asyncio
async def test_patch_data_sends_if_match_and_task_id():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert (
            str(request.url)
            == "http://workflow.local/api/v1/runs/run-1/nodes/node-1/data?task_id=task-1"
        )
        assert request.headers["If-Match"] == "etag-1"
        assert request.read() == b'{"artifacts":[{"file_id":"file-1"}]}'
        return httpx.Response(200, json={"status": "ok", "data": {"updated": True}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    result = await service.patch_data(
        PatchDataRequest(
            run_id="run-1",
            node_id="node-1",
            artifacts=[{"file_id": "file-1"}],
            if_match="etag-1",
            task_id="task-1",
        )
    )

    assert result.data == {"updated": True}


@pytest.mark.asyncio
async def test_submit_posts_expected_path():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert (
            str(request.url)
            == "http://workflow.local/api/v1/runs/run-1/nodes/node-1/submit"
        )
        return httpx.Response(
            200, json={"status": "ok", "data": {"status": "submitted"}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    result = await service.submit(
        SubmitRequest(
            run_id="run-1",
            node_id="node-1",
        )
    )

    assert result.data == {"status": "submitted"}


@pytest.mark.asyncio
async def test_raises_wrapped_error_on_http_failure():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"status": "error", "message": "bad request"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    with pytest.raises(WorkflowControlPlaneError, match="400"):
        await service.submit(
            SubmitRequest(
                run_id="run-1",
                node_id="node-1",
            )
        )


@pytest.mark.asyncio
async def test_non_json_http_error_raises_wrapped_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream failure")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    with pytest.raises(WorkflowControlPlaneError) as exc_info:
        await service.submit(
            SubmitRequest(
                run_id="run-1",
                node_id="node-1",
            )
        )

    assert exc_info.value.code == "NON_JSON_HTTP_ERROR"
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_unknown_response_status_raises_wrapped_error():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "pending", "data": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    with pytest.raises(WorkflowControlPlaneError) as exc_info:
        await service.submit(
            SubmitRequest(
                run_id="run-1",
                node_id="node-1",
            )
        )

    assert exc_info.value.code == "MALFORMED_WORKFLOW_SUCCESS_RESPONSE"
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_start_run_wraps_malformed_success_response():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "data": {"status": "running"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    result = await service.start_run(
        StartRunRequest(
            workflow_id="wf-1",
            predefined_run_id="run-1",
        )
    )

    assert result.status == "running"
    assert result.model_extra == {}


@pytest.mark.asyncio
async def test_error_response_propagates_error_envelope():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "status": "error",
                "error": {
                    "code": "BAD_REQUEST",
                    "message": "invalid payload",
                    "details": {"field": "run_id"},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    with pytest.raises(WorkflowControlPlaneError) as exc_info:
        await service.submit(
            SubmitRequest(
                run_id="run-1",
                node_id="node-1",
            )
        )

    assert exc_info.value.code == "BAD_REQUEST"
    assert exc_info.value.message == "invalid payload"
    assert exc_info.value.details == {"field": "run_id"}
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_stop_run_posts_expected_path():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://workflow.local/api/v1/runs/run-1/cancel"
        return httpx.Response(200, json={"status": "ok", "data": {"cancelled": True}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    result = await service.stop_run(StopRunRequest(run_id="run-1"))

    assert result.cancelled is True
    assert result.to_tool_result() == {
        "cancelled": True,
        "error": None,
        "error_code": None,
        "error_details": {},
    }


@pytest.mark.asyncio
async def test_stop_run_raises_wrapped_error_when_run_not_found():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "status": "error",
                "error": {
                    "code": "RUN_NOT_FOUND",
                    "message": "run not found",
                    "details": {"run_id": "run-1"},
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = WorkflowControlPlaneClient("http://workflow.local", client=client)

    with pytest.raises(WorkflowControlPlaneError) as exc_info:
        await service.stop_run(StopRunRequest(run_id="run-1"))

    assert exc_info.value.code == "RUN_NOT_FOUND"
    assert exc_info.value.status_code == 409
