from __future__ import annotations

from typing import Any

from agent_sdk import tool
from pydantic import ValidationError as PydanticValidationError

from app.layer4_frameworks.clients.workflow_control_plane.models import SubmitRequest
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneClient,
    WorkflowControlPlaneError,
)


def build_workflow_submit_tool(
    client: WorkflowControlPlaneClient,
) -> Any:
    @tool
    async def workflow_submit_tool(
        run_id: str,
        node_id: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Submit a workflow node task through the workflow control plane."""

        try:
            if client is None:
                raise RuntimeError("Workflow control-plane client is not bound")

            response = await client.submit(
                SubmitRequest(
                    run_id=run_id,
                    node_id=node_id,
                    task_id=task_id,
                )
            )
            return response.to_tool_result()

        except PydanticValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_SUBMIT_REQUEST",
                    "message": "Invalid submit request",
                    "details": {
                        "errors": _format_pydantic_errors(exc),
                    },
                },
            }

        except WorkflowControlPlaneError as exc:
            return {
                "ok": False,
                "error": {
                    "code": exc.code or "WORKFLOW_CONTROL_PLANE_ERROR",
                    "message": exc.message,
                    "details": exc.details,
                    "status_code": exc.status_code,
                },
            }

    return workflow_submit_tool


def _format_pydantic_errors(
    exc: PydanticValidationError,
) -> list[dict[str, Any]]:
    return [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]
