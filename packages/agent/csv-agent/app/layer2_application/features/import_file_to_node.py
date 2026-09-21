import logging
from typing import Any, Dict

from app.layer2_application.interfaces.workflow_node_gateway import IWorkflowNodeGateway

logger = logging.getLogger(__name__)


async def _import_file_to_node_impl(
    run_id: str,
    node_id: str,
    file_id: str,
    *,
    workflow_node_gateway: IWorkflowNodeGateway,
) -> Dict[str, Any]:
    table_field_key = await workflow_node_gateway.get_table_field_key(run_id, node_id)
    logger.info("Updating node input, resolved table field: %s", table_field_key)
    return await workflow_node_gateway.assign_file_to_node(
        run_id=run_id,
        node_id=node_id,
        file_id=file_id,
        target_field=table_field_key,
    )


async def import_file_to_node(
    run_id: str,
    node_id: str,
    uploaded_file_id: str,
    *,
    workflow_node_gateway: IWorkflowNodeGateway,
) -> Dict[str, Any]:
    """Update a workflow node input with an uploaded file."""
    return await _import_file_to_node_impl(
        run_id,
        node_id,
        uploaded_file_id,
        workflow_node_gateway=workflow_node_gateway,
    )
