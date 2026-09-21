from typing import Any

from langchain_core.tools import BaseTool

from app.layer4_frameworks.clients.workflow_control_plane.workflow_control_plane_client import (
    WorkflowControlPlaneClient,
)
from app.layer4_frameworks.tools.echo_tool import echo_tool
from app.layer4_frameworks.tools.workflow_patch_data_tool import (
    build_workflow_patch_data_tool,
)
from app.layer4_frameworks.tools.workflow_start_run_tool import (
    build_workflow_start_run_tool,
)
from app.layer4_frameworks.tools.workflow_stop_tool import build_workflow_stop_tool
from app.layer4_frameworks.tools.workflow_submit_tool import build_workflow_submit_tool


def _normalize_tool_name(tool: Any) -> None:
    tool_name = getattr(tool, "name", "")
    if isinstance(tool_name, str) and tool_name.endswith("_tool"):
        object.__setattr__(tool, "name", tool_name.removesuffix("_tool"))


def _sanitize_tool_input_schema(tool: Any) -> None:
    schema_factory = getattr(tool, "get_input_schema", None)
    if not callable(schema_factory):
        return

    def _get_input_schema() -> dict[str, Any]:
        schema = schema_factory()
        if not isinstance(schema, dict):
            schema = getattr(schema, "model_json_schema", lambda: {})()
        if not isinstance(schema, dict):
            return {"properties": {}}

        sanitized: dict[str, Any] = {}
        properties = schema.get("properties")
        if isinstance(properties, dict):
            sanitized["properties"] = properties

        required = schema.get("required")
        if isinstance(required, list):
            sanitized["required"] = required

        return sanitized or {"properties": {}}

    object.__setattr__(tool, "get_input_schema", _get_input_schema)


def build_local_tools(
    workflow_control_plane_client: WorkflowControlPlaneClient,
) -> list[BaseTool]:
    tools = [
        echo_tool,
        build_workflow_start_run_tool(workflow_control_plane_client),
        build_workflow_patch_data_tool(workflow_control_plane_client),
        build_workflow_submit_tool(workflow_control_plane_client),
        build_workflow_stop_tool(workflow_control_plane_client),
    ]

    for tool in tools:
        _normalize_tool_name(tool)
        _sanitize_tool_input_schema(tool)

    return tools
