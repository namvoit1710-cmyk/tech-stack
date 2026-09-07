"""
Example MCP tool module that mirrors old REST controller flow.

Purpose:
- Keep existing REST endpoints unchanged.
- Show a minimal pattern to add MCP tools by reusing the same use case and command logic.

Note:
- This file is an example and is not auto-registered.
- To enable these tools, import this module in main startup code and call register_example_tools(...).
"""

import json
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import (
    DataTransformationCommand,
)


class TransformLikeRestInput(BaseModel):
    """Input payload matching old REST style fields."""

    file_id: Optional[str] = Field(default=None)
    file_path: Optional[str] = Field(default=None)
    file_format: str = Field(default="csv")
    output_format: str = Field(default="csv")
    rules: List[Dict[str, Any]] = Field(default_factory=list)
    version_id: Optional[str] = Field(default=None)


def _resolve_file_id(file_id: Optional[str], file_path: Optional[str]) -> str:
    resolved = file_id or file_path
    if not resolved:
        raise ValueError("file_id is required")
    return resolved


async def run_transform_data_shared(
    get_usecase: Callable[[str], Any],
    dto: TransformLikeRestInput,
) -> Dict[str, Any]:
    """
    Shared, transport-neutral handler.

    This is the part you should reuse in both REST and MCP wrappers.
    """
    resolved_file_id = _resolve_file_id(dto.file_id, dto.file_path)
    usecase = get_usecase("data_transformation_usecase")
    if not usecase:
        raise RuntimeError("UseCase 'data_transformation_usecase' not available")

    command = DataTransformationCommand(
        file_id=resolved_file_id,
        file_format=dto.file_format,
        output_format=dto.output_format,
        rules=dto.rules,
        version_id=dto.version_id,
    )

    result = await usecase.execute(command)

    if not result.success:
        stale_version = bool(result.result and getattr(result.result, "stale_version", False))
        return {
            "success": False,
            "message": result.message,
            "error_type": "stale_version" if stale_version else "bad_request",
            "requested_version_id": dto.version_id,
            "current_version_id": (
                getattr(result.result, "current_version_id", None) if result.result else None
            ),
            "result": asdict(result.result) if result.result else None,
        }

    return {
        "success": True,
        "message": result.message,
        "result": asdict(result.result) if result.result else None,
    }


def register_example_tools(
    mcp_server: Any,
    get_usecase: Callable[[str], Any],
) -> None:
    """
    Register example MCP tools.

    Parameters:
    - mcp_server: MCP server object, for example from create_mcp_server(...)
    - get_usecase: function like _get_usecase(name) that returns use case from container
    """

    @mcp_server.tool()
    async def transform_file_from_rest_style(
        file_id: Optional[str] = None,
        file_path: Optional[str] = None,
        file_format: str = "csv",
        output_format: str = "csv",
        rules: Optional[List[Dict[str, Any]]] = None,
        version_id: Optional[str] = None,
    ) -> str:
        """
        Example MCP tool equivalent to old REST transform endpoint.

        This intentionally accepts file_id OR file_path, mirroring existing REST behavior.
        """
        dto = TransformLikeRestInput(
            file_id=file_id,
            file_path=file_path,
            file_format=file_format,
            output_format=output_format,
            rules=rules or [],
            version_id=version_id,
        )

        payload = await run_transform_data_shared(get_usecase=get_usecase, dto=dto)
        return json.dumps(payload, default=str)
