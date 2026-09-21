import logging
from typing import Any, Dict, Optional

import httpx
from agent_sdk import tool

from app.layer4_frameworks.config.settings_adapter import AppSettingsAdapter


logger = logging.getLogger(__name__)


def _get_workflow_api_base_url() -> str:
    settings = AppSettingsAdapter()
    return settings.WORKFLOW_API_BASE_URL.rstrip("/")


async def _import_file_to_node_impl(
    run_id: str,
    node_id: str,
    file_id: str,
) -> Dict[str, Any]:
    table_field_key = await check_table_field_key(run_id, node_id)
    base_url = _get_workflow_api_base_url()
    
    url = f"{base_url}/runs/{run_id}/nodes/{node_id}/input"
    body: Dict[str, Any] = {
        "auto_rerun": False,
        "file_id": file_id
    }
    if table_field_key:
        body["target_field"] = table_field_key

    headers = {
        "Content-Type": "application/json",
        "x-tenant-id": "system",
        "user-agent": "file-agent/1.0",
    }
    logger.info("Updating node input with body: %s", body)

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.patch(url, headers=headers, json=body)
        if resp.status_code == 200:
            resp.raise_for_status()
            return {
                "message": "Upload file successfully assigned to node input",
                "file_id": file_id
            }
        else:
            logger.error("API call failed with status code %s and response: %s", resp.status_code, resp.text)
            return {
                "error": "Request failed",
                "status_code": resp.status_code,
                "response": resp.text
            }

async def check_table_field_key(
    run_id: str,
    node_id: str,
) -> Optional[str]:
    base_url = _get_workflow_api_base_url()
    
    url = f"{base_url}/runs/{run_id}/nodes/{node_id}/input-data"

    headers = {
        "Content-Type": "application/json",
        "x-tenant-id": "system",
        "user-agent": "file-agent/1.0",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            resp.raise_for_status()
            response_data = resp.json()
            input_schema = response_data.get("data", {}).get("input_schema")
            if not input_schema:
                return None

            first_input = input_schema[0]
            field_config = first_input.get("fieldConfig", {})
            if field_config.get("fieldWrapper") == "TableWrapper":
                key = first_input.get("key")
                return key if isinstance(key, str) else None

            return None

        logger.error("API call failed with status code %s and response: %s", resp.status_code, resp.text)
        return None

@tool("import_file_to_node", internal=True)
async def import_file_to_node(
    run_id: str,
    node_id: str,
    uploaded_file_id: str,
) -> Dict[str, Any]:
    """Update a workflow node/table input with an uploaded file.
    Use this tool only when the user explicitly asks to import, attach, assign, upload, or apply a file to a workflow or node or node is data edit, or node has table inside.
    Do not use this tool for plain revert, undo, turn back, or restore previous version requests.
    After a revert, only use this tool if the user explicitly asks to apply the reverted file back to the workflow node.

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
    return await _import_file_to_node_impl(run_id, node_id, uploaded_file_id)