from __future__ import annotations

from typing import Any

from agent_sdk import tool
from pydantic import ValidationError as PydanticValidationError

from app.layer4_frameworks.clients.workflow_control_plane.models import StopRunRequest
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneClient,
    WorkflowControlPlaneError,
)


def build_workflow_stop_tool(
    client: WorkflowControlPlaneClient,
) -> Any:
    @tool
    async def workflow_stop_tool(
        run_id: str,
    ) -> dict[str, Any]:
        """Stop (cancel) a running workflow run through the workflow control plane.
        Args:
            run_id: The ID of the workflow run to stop.
        """

        try:
            if client is None:
                raise RuntimeError("Workflow control-plane client is not bound")

            response = await client.stop_run(
                StopRunRequest(
                    run_id=run_id,
                )
            )
            return response.to_tool_result()

        except PydanticValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_STOP_RUN_REQUEST",
                    "message": "Invalid stop run request",
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

    return workflow_stop_tool


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
