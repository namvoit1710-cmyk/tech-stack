from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.layer4_frameworks.clients.workflow_control_plane.models import (
    GetDataRequest,
    GetDataResponse,
    PatchDataRequest,
    PatchDataResponse,
    StartRunRequest,
    StopRunRequest,
    SubmitRequest,
    SubmitResponse,
)
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneError,
)
from app.layer4_frameworks.tools.workflow_patch_data_tool import (
    build_workflow_patch_data_tool,
)
from app.layer4_frameworks.tools.workflow_start_run_tool import (
    build_workflow_start_run_tool,
)
from app.layer4_frameworks.tools.workflow_stop_tool import (
    build_workflow_stop_tool,
)
from app.layer4_frameworks.tools.workflow_submit_tool import (
    build_workflow_submit_tool,
)
from app.layer4_frameworks.dependency.ioc_container import build_dependencies
import app.layer4_frameworks.dependency.ioc_container as ioc_container_module
from app.layer4_frameworks.config.app_config import settings

@pytest.mark.asyncio
async def test_workflow_start_run_tool_delegates_to_bound_client():
    class _StartRunResponse:
        def to_tool_result(self) -> dict:
            return {"run_id": "run-1", "status": "running"}

    client = AsyncMock()
    client.start_run.return_value = _StartRunResponse()
    workflow_start_run_tool = build_workflow_start_run_tool(client)

    result = await workflow_start_run_tool.ainvoke(
        {
            "workflow_id": "wf-1",
            "predefined_run_id": "run-1",
            "input_data": {"input": 1},
            "execution_mode": "sync",
            "metadata": {"source": "agent", "target_topic": "business.topic"},
        }
    )

    assert result == {"run_id": "run-1", "status": "running"}
    client.start_run.assert_awaited_once_with(
        StartRunRequest(
            workflow_id="wf-1",
            predefined_run_id="run-1",
            input_data={"input": 1},
            execution_mode="sync",
            metadata={"source": settings.SOURCE, "target_topic": settings.TARGET_TOPIC},
        )
    )


@pytest.mark.asyncio
async def test_workflow_start_run_tool_includes_runtime_credentials_when_token_is_provided():
    class _StartRunResponse:
        def to_tool_result(self) -> dict:
            return {"run_id": "run-1", "status": "running"}

    client = AsyncMock()
    client.start_run.return_value = _StartRunResponse()
    workflow_start_run_tool = build_workflow_start_run_tool(client)

    result = await workflow_start_run_tool.ainvoke(
        {
            "workflow_id": "wf-1",
            "predefined_run_id": "run-1",
            "token": "token-1",
            "input_data": {"input": 1},
            "execution_mode": "sync",
            "metadata": {"source": "agent", "target_topic": "business.topic"},
        }
    )

    assert result == {"run_id": "run-1", "status": "running"}
    client.start_run.assert_awaited_once_with(
        StartRunRequest(
            workflow_id="wf-1",
            predefined_run_id="run-1",
            input_data={"input": 1},
            execution_mode="sync",
            runtime_credentials={
                "default": {
                    "type": "bearer",
                    "config": {"token": "token-1"},
                }
            },
            metadata={"source": settings.SOURCE, "target_topic": settings.TARGET_TOPIC},
        )
    )


@pytest.mark.asyncio
async def test_workflow_start_run_tool_returns_control_plane_error():
    client = AsyncMock()
    client.start_run.side_effect = WorkflowControlPlaneError(
        502,
        code="BAD_START",
        message="start failed",
        details={"detail": "bad"},
    )
    workflow_start_run_tool = build_workflow_start_run_tool(client)

    result = await workflow_start_run_tool.ainvoke(
        {
            "workflow_id": "wf-1",
            "predefined_run_id": "run-1",
            "input_data": {"input": 1},
            "metadata": {"source": "agent", "target_topic": "business.topic"},
        }
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "BAD_START",
            "message": "start failed",
            "details": {"detail": "bad"},
            "status_code": 502,
            # "metadata": {"source": "agent", "target_topic": "business.topic"},
        },
    }


@pytest.mark.asyncio
async def test_workflow_patch_data_tool_fetches_if_match_when_missing():
    client = AsyncMock()
    client.get_data.return_value = GetDataResponse(
        data={"artifacts": []}, etag="etag-1"
    )
    client.patch_data.return_value = PatchDataResponse(data={"status": "patched"})
    workflow_patch_data_tool = build_workflow_patch_data_tool(client)

    result = await workflow_patch_data_tool.ainvoke(
        {
            "run_id": "run-1",
            "node_id": "node-1",
            "data": {"input": "hello", "input_1": "world"},
        }
    )

    assert result == {"status": "patched"}
    client.get_data.assert_awaited_once_with(
        GetDataRequest(
            run_id="run-1",
            node_id="node-1",
            task_id=None,
        )
    )
    client.patch_data.assert_awaited_once_with(
        PatchDataRequest(
            run_id="run-1",
            node_id="node-1",
            artifacts=[
                {
                    "artifact_id": "__root__",
                    "operations": [
                        {
                            "operation": "insert",
                            "data": {"input": "hello", "input_1": "world"},
                        }
                    ],
                }
            ],
            if_match="etag-1",
            task_id=None,
        )
    )


@pytest.mark.asyncio
async def test_workflow_patch_data_tool_returns_missing_if_match_when_etag_missing():
    client = AsyncMock()
    client.get_data.return_value = GetDataResponse(data={"artifacts": []}, etag=None)
    workflow_patch_data_tool = build_workflow_patch_data_tool(client)

    result = await workflow_patch_data_tool.ainvoke(
        {
            "run_id": "run-1",
            "node_id": "node-1",
            "data": {"input": "hello"},
        }
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "MISSING_IF_MATCH",
            "message": "Unable to resolve If-Match for patch_data",
            "details": {},
        },
    }
    client.get_data.assert_awaited_once()
    client.patch_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_patch_data_tool_validates_required_fields():
    client = AsyncMock()
    workflow_patch_data_tool = build_workflow_patch_data_tool(client)

    with pytest.raises(ValidationError):
        await workflow_patch_data_tool.ainvoke(
            {
                "node_id": "node-1",
                "data": {"input": "hello"},
            }
        )

    client.get_data.assert_not_awaited()
    client.patch_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_workflow_patch_data_tool_returns_control_plane_error():
    client = AsyncMock()
    client.get_data.return_value = GetDataResponse(
        data={"artifacts": []}, etag="etag-1"
    )
    client.patch_data.side_effect = WorkflowControlPlaneError(
        409,
        code="PATCH_CONFLICT",
        message="patch failed",
        details={"detail": "conflict"},
    )
    workflow_patch_data_tool = build_workflow_patch_data_tool(client)

    result = await workflow_patch_data_tool.ainvoke(
        {
            "run_id": "run-1",
            "node_id": "node-1",
            "data": {"input": "hello"},
        }
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "PATCH_CONFLICT",
            "message": "patch failed",
            "details": {"detail": "conflict"},
            "status_code": 409,
        },
    }


@pytest.mark.asyncio
async def test_workflow_submit_tool_delegates_to_bound_client():
    client = AsyncMock()
    client.submit.return_value = SubmitResponse(data={"status": "submitted"})
    workflow_submit_tool = build_workflow_submit_tool(client)

    result = await workflow_submit_tool.ainvoke(
        {"run_id": "run-1", "node_id": "node-1", "task_id": "task-1"}
    )

    assert result == {"status": "submitted"}
    client.submit.assert_awaited_once_with(
        SubmitRequest(
            run_id="run-1",
            node_id="node-1",
            task_id="task-1",
        )
    )


@pytest.mark.asyncio
async def test_workflow_submit_tool_returns_control_plane_error():
    client = AsyncMock()
    client.submit.side_effect = WorkflowControlPlaneError(
        400,
        code="BAD_SUBMIT",
        message="submit failed",
        details={"detail": "bad submit"},
    )
    workflow_submit_tool = build_workflow_submit_tool(client)

    result = await workflow_submit_tool.ainvoke(
        {"run_id": "run-1", "node_id": "node-1", "task_id": "task-1"}
    )

    assert result == {
        "ok": False,
        "error": {
            "code": "BAD_SUBMIT",
            "message": "submit failed",
            "details": {"detail": "bad submit"},
            "status_code": 400,
        },
    }


@pytest.mark.asyncio
async def test_build_dependencies_binds_workflow_control_plane_client(monkeypatch):
    class _StartRunResponse:
        def to_tool_result(self) -> dict:
            return {"run_id": "run-1", "status": "running"}

    client = AsyncMock()
    client.start_run.return_value = _StartRunResponse()
    monkeypatch.setattr(
        ioc_container_module,
        "build_workflow_control_plane_client",
        lambda: client,
    )

    dependencies = build_dependencies()
    workflow_start_run = next(
        tool
        for tool in dependencies["worker_tools"]
        if getattr(tool, "name", "") == "workflow_start_run"
    )

    result = await workflow_start_run.ainvoke(
        {
            "workflow_id": "wf-1",
            "predefined_run_id": "run-1",
        }
    )

    assert result == {"run_id": "run-1", "status": "running"}
    assert dependencies["workflow_control_plane_client"] is client
    client.start_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_workflow_stop_tool_delegates_to_bound_client():
    class _StopRunResponse:
        def to_tool_result(self) -> dict:
            return {
                "cancelled": True,
                "error": None,
                "error_code": None,
                "error_details": {},
            }

    client = AsyncMock()
    client.stop_run.return_value = _StopRunResponse()
    workflow_stop_tool = build_workflow_stop_tool(client)

    result = await workflow_stop_tool.ainvoke({"run_id": "run-1"})

    assert result == {
        "cancelled": True,
        "error": None,
        "error_code": None,
        "error_details": {},
    }
    client.stop_run.assert_awaited_once_with(StopRunRequest(run_id="run-1"))


@pytest.mark.asyncio
async def test_workflow_stop_tool_returns_control_plane_error():
    client = AsyncMock()
    client.stop_run.side_effect = WorkflowControlPlaneError(
        status_code=409,
        code="INVALID_RUN_STATUS",
        message="run is not cancellable",
        details={"current_status": "COMPLETED"},
    )
    workflow_stop_tool = build_workflow_stop_tool(client)

    result = await workflow_stop_tool.ainvoke({"run_id": "run-1"})

    assert result == {
        "ok": False,
        "error": {
            "code": "INVALID_RUN_STATUS",
            "message": "run is not cancellable",
            "details": {"current_status": "COMPLETED"},
            "status_code": 409,
        },
    }


@pytest.mark.asyncio
async def test_workflow_stop_tool_validates_required_fields():
    client = AsyncMock()
    workflow_stop_tool = build_workflow_stop_tool(client)

    result = await workflow_stop_tool.ainvoke({"run_id": "   "})

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_STOP_RUN_REQUEST"
    client.stop_run.assert_not_awaited()
