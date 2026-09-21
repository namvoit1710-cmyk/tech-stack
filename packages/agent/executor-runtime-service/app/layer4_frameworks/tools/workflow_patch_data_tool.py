from __future__ import annotations

from typing import Any

from agent_sdk import tool
from pydantic import ValidationError as PydanticValidationError

from app.layer4_frameworks.clients.workflow_control_plane.models import (
    GetDataRequest,
    PatchDataRequest,
)
from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneClient,
    WorkflowControlPlaneError,
)


def build_workflow_patch_data_tool(
    client: WorkflowControlPlaneClient,
) -> Any:
    @tool
    async def workflow_patch_data_tool(
        run_id: str,
        node_id: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Update / fill the input fields of the current workflow form node.

        Call this whenever the user asks to set, change, update or fill a field
        shown on the form (e.g. "Data Input ID", "Data Input Description",
        "Object Type", or "description is abc"). It patches the node's data on the
        workflow control plane so the new values are reflected back on the form.

        Args:
            run_id: The current workflow run id.
            node_id: The form node id whose data is being updated.
            data: Mapping of field name -> value to set on the node
                (e.g. {"description": "abc", "objectType": "MATERIAL"}).
        """

        try:
            request = PatchDataRequest(
                run_id=run_id,
                node_id=node_id,
                artifacts=_build_patch_artifacts(data),
            )

            if client is None:
                raise RuntimeError("Workflow control-plane client is not bound")

            if not request.if_match:
                get_data_response = await client.get_data(
                    GetDataRequest(
                        run_id=request.run_id,
                        node_id=request.node_id,
                        task_id=None,
                    )
                )
                if not get_data_response.etag:
                    return {
                        "ok": False,
                        "error": {
                            "code": "MISSING_IF_MATCH",
                            "message": "Unable to resolve If-Match for patch_data",
                            "details": {},
                        },
                    }

                request = PatchDataRequest(
                    run_id=request.run_id,
                    node_id=request.node_id,
                    artifacts=request.artifacts,
                    if_match=get_data_response.etag,
                    task_id=None,
                )

            response = await client.patch_data(request)
            return response.to_tool_result()

        except PydanticValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_PATCH_DATA_REQUEST",
                    "message": "Invalid patch data request",
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

    return workflow_patch_data_tool


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


def _build_patch_artifacts(
    data: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    return [
        {
            "artifact_id": "__root__",
            "operations": [
                {
                    "operation": "insert",
                    "data": data,
                }
            ],
        }
    ]
