"""Controller for internal functions (trigger workflow, call API).

Internal functions are design-time / runtime actions that:
- Dropdown selection: "Trigger Workflow" or "Call API"
- Always return result as file_id with array of objects format
"""

import json
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.layer3_adapters.controllers.restful.v1.dtos.response_envelope import error_response


router = APIRouter()


# ── Predefined API Actions ────────────────────────────────────────
# Each action has: name, description, method, endpoint_url, payload template

PREDEFINED_API_ACTIONS = [
    {
        "name": "get_list_object_type",
        "description": "Get list of object types from MDG system",
        "method": "GET",
        "endpoint_url": "/api/v1/object-types",
        "payload": {},
    },
    {
        "name": "get_list_template",
        "description": "Get list of available templates",
        "method": "GET",
        "endpoint_url": "/api/v1/templates",
        "payload": {},
    },
    {
        "name": "get_list_validation_rules",
        "description": "Get list of validation rules",
        "method": "GET",
        "endpoint_url": "/api/v1/validation-rules",
        "payload": {},
    },
    {
        "name": "get_list_workflows",
        "description": "Get list of available workflows",
        "method": "GET",
        "endpoint_url": "/api/v1/workflows",
        "payload": {},
    },
]


# ── Request DTOs ──────────────────────────────────────────────────


class TriggerWorkflowRequest(BaseModel):
    """Request to trigger a workflow."""
    workflow_id: str
    input_data: dict[str, Any] = {}
    wait_for_completion: bool = False
    timeout_seconds: int = 300


class CallApiRequest(BaseModel):
    """Request to call a predefined API action."""
    action_name: str
    params: dict[str, Any] = {}


# ── Dependency helpers ────────────────────────────────────────────


def _get_trigger_workflow_usecase(request: Request) -> Any:
    return request.app.state.container.get("internal_trigger_workflow")


def _get_call_api_usecase(request: Request) -> Any:
    return request.app.state.container.get("internal_call_api")


def _get_file_service_client(request: Request) -> Any:
    return request.app.state.container.get("file_service_client")


# ── Endpoints ─────────────────────────────────────────────────────


@router.get("/types")
def list_function_types() -> Any:
    """List available internal function types."""
    return [
        {
            "type": "trigger_workflow",
            "label": "Trigger Workflow",
            "description": "Execute a workflow and return result as file",
            "config_schema": [
                {"key": "workflow_id", "type": "string", "required": True, "label": "Workflow ID"},
                {"key": "input_data", "type": "object", "required": False, "label": "Input Data"},
                {"key": "wait_for_completion", "type": "boolean", "required": False, "label": "Wait for Completion", "default": False},
            ],
        },
        {
            "type": "call_api",
            "label": "Call API",
            "description": "Call a predefined API action and return result as file",
            "config_schema": [
                {"key": "action_name", "type": "string", "required": True, "label": "Action"},
                {"key": "params", "type": "object", "required": False, "label": "Parameters"},
            ],
        },
    ]


@router.get("/api-actions")
def list_api_actions() -> Any:
    """List available predefined API actions for 'call_api' function type."""
    return PREDEFINED_API_ACTIONS


@router.post("/trigger-workflow")
async def trigger_workflow(
    payload: TriggerWorkflowRequest,
    request: Request,
) -> Any:
    """Trigger a workflow execution.
    
    If wait_for_completion=False: Returns immediately with run_id.
    Frontend should subscribe to socket room and wait for run.completed event.
    
    If wait_for_completion=True: Waits for workflow completion and returns file_id.
    (Not recommended for long-running workflows)
    """
    usecase = _get_trigger_workflow_usecase(request)
    if not usecase:
        return error_response("SERVICE_UNAVAILABLE", "Trigger workflow usecase not configured", status_code=503)
    
    result = await usecase.execute(
        workflow_id=payload.workflow_id,
        input_data=payload.input_data,
        wait_for_completion=payload.wait_for_completion,
        timeout_seconds=payload.timeout_seconds,
    )
    
    if result.get("error"):
        return error_response("BAD_REQUEST", result["error"])
    
    return result


@router.post("/call-api")
async def call_api(
    payload: CallApiRequest,
    request: Request,
) -> Any:
    """Call a predefined API action and return result as file_id.
    
    The result is always stored as a JSON file with array of objects format.
    """
    usecase = _get_call_api_usecase(request)
    if not usecase:
        return error_response("SERVICE_UNAVAILABLE", "Call API usecase not configured", status_code=503)
    
    # Find action config
    action_config = next(
        (a for a in PREDEFINED_API_ACTIONS if a["name"] == payload.action_name),
        None,
    )
    if not action_config:
        return error_response("BAD_REQUEST", f"Unknown action: {payload.action_name}")
    
    result = await usecase.execute(
        action_config=action_config,
        params=payload.params,
    )
    
    if result.get("error"):
        return error_response("BAD_REQUEST", result["error"])
    
    return result


@router.get("/workflows/{workflow_id}/output")
async def get_workflow_output_as_file(
    workflow_id: str,
    run_id: str,
    request: Request,
) -> Any:
    """Get workflow run output as file_id.
    
    Called by frontend after receiving run.completed socket event.
    Extracts run output and uploads to file storage.
    """
    from app.layer2_application.features.get_run_status.use_cases.get_run_status_use_case import (
        GetRunStatusCommand,
    )
    
    get_run_status = request.app.state.container.get("get_run_status")
    file_service = _get_file_service_client(request)
    
    if not get_run_status or not file_service:
        return error_response("SERVICE_UNAVAILABLE", "Required services not configured", status_code=503)
    
    # Get run status and output
    command = GetRunStatusCommand(run_id=run_id)
    run_result = await get_run_status.execute(command)
    
    if not run_result.id:
        return error_response("NOT_FOUND", f"Run not found: {run_id}", status_code=404)
    
    if run_result.status != "COMPLETED":
        return error_response("BAD_REQUEST", f"Run not completed. Status: {run_result.status}")
    
    # Extract output data from run context
    output_data = run_result.outputs or {}
    
    # Ensure array format
    if isinstance(output_data, dict):
        output_data = [output_data]
    elif not isinstance(output_data, list):
        output_data = [{"value": output_data}]
    
    # Upload to file storage
    file_content = json.dumps(output_data, ensure_ascii=False, indent=2)
    filename = f"workflow_{workflow_id}_run_{run_id}_output.json"
    
    upload_result = await file_service.upload_file(
        file_bytes=file_content.encode("utf-8"),
        filename=filename,
        content_type="application/json",
    )
    
    return {
        "file_id": upload_result.get("file_id"),
        "record_count": len(output_data),
        "run_id": run_id,
        "workflow_id": workflow_id,
    }
