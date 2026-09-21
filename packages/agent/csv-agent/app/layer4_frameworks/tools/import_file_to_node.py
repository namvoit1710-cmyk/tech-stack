from typing import Any, Dict

from agent_sdk import tool

from app.layer2_application.features.import_file_to_node import (
    _import_file_to_node_impl,
)
from app.layer2_application.interfaces.workflow_node_gateway import IWorkflowNodeGateway


def make_import_file_to_node_tool(*, workflow_node_gateway: IWorkflowNodeGateway):
    @tool(internal=True)
    async def import_file_to_node(
        run_id: str,
        node_id: str,
        uploaded_file_id: str,
    ) -> Dict[str, Any]:
        """
        Update a workflow node input with an uploaded file.

        Use this tool only when the user explicitly asks to import, attach,
        assign, upload, or apply a file to a workflow or node. Do not use this
        tool for plain revert, undo, turn back, or restore previous version
        requests. After a revert, only use this tool if the user explicitly asks
        to apply the reverted file back to the workflow node.

        Args:
            run_id: Required workflow run identifier.
            node_id: Required workflow node identifier.
            uploaded_file_id: Required uploaded file identifier to assign to the node input.

        Returns:
            {
                "file_id": str,
                "message": str
            }
        """
        return await _import_file_to_node_impl(
            run_id,
            node_id,
            uploaded_file_id,
            workflow_node_gateway=workflow_node_gateway,
        )

    return import_file_to_node


__all__ = ["make_import_file_to_node_tool"]
