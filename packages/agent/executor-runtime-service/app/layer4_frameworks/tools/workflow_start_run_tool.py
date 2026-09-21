from __future__ import annotations

from typing import Any

from agent_sdk import tool
from pydantic import ValidationError as PydanticValidationError

from app.layer4_frameworks.clients.workflow_control_plane.models import StartRunRequest
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneClient,
    WorkflowControlPlaneError,
)
from app.layer4_frameworks.config.app_config import settings


def build_workflow_start_run_tool(
    client: WorkflowControlPlaneClient,
) -> Any:
    @tool
    async def workflow_start_run_tool(
        workflow_id: str,
        predefined_run_id: str,
        token: str | None = None,
        input_data: dict[str, Any] | None = None,
        execution_mode: str = "auto",
    ) -> dict[str, Any]:
        """Start a workflow run through the workflow control plane.
        Args:
            workflow_id: The ID of the workflow to start.
            predefined_run_id: A predefined run ID to use for the workflow run.
            token: Optional bearer token used for runtime credentials.
            input_data: Optional input data to pass to the workflow run.
            execution_mode: The execution mode for the workflow run (e.g., "auto", "manual").
        """

        try:
            runtime_credentials: dict[str, Any] = {}
            if token is not None:
                runtime_credentials = {
                    "default": {
                        "type": "bearer",
                        "config": {"token": token},
                    }
                }

            request = StartRunRequest(
                workflow_id=workflow_id,
                predefined_run_id=predefined_run_id,
                input_data=input_data or {},
                execution_mode=execution_mode,
                runtime_credentials=runtime_credentials,
                # NOTE: when testing in local, must change the value in 'target_topic'
                metadata={"source": settings.SOURCE, "target_topic": settings.TARGET_TOPIC},
            )

            if client is None:
                raise RuntimeError("Workflow control-plane client is not bound")

            response = await client.start_run(request)

            return response.to_tool_result()

        except PydanticValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_START_RUN_REQUEST",
                    "message": "Invalid start run request",
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

    return workflow_start_run_tool


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
