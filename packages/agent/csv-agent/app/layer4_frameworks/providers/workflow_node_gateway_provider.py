from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

from app.layer2_application.interfaces.workflow_node_gateway import IWorkflowNodeGateway

logger = logging.getLogger(__name__)


class WorkflowNodeGatewayProvider(IWorkflowNodeGateway):
    """Layer 4 adapter for workflow node read/update operations."""

    def __init__(self, settings_provider) -> None:
        self._settings_provider = settings_provider

    def _base_url(self) -> str:
        return self._settings_provider.get_workflow_api_base_url()

    @staticmethod
    def _headers() -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-tenant-id": "system",
            "user-agent": "csv-agent/1.0",
        }

    async def get_table_field_key(self, run_id: str, node_id: str) -> Optional[str]:
        url = f"{self._base_url()}/runs/{run_id}/nodes/{node_id}/input-data"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=self._headers())
            if resp.status_code != 200:
                logger.error(
                    "Workflow input-data request failed: %s %s",
                    resp.status_code,
                    resp.text,
                )
                return None

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

    async def assign_file_to_node(
        self,
        run_id: str,
        node_id: str,
        file_id: str,
        target_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{self._base_url()}/runs/{run_id}/nodes/{node_id}/input"
        body: Dict[str, Any] = {
            "auto_rerun": False,
            "file_id": file_id,
        }
        if target_field:
            body["target_field"] = target_field

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(url, headers=self._headers(), json=body)
            if resp.is_success:
                resp.raise_for_status()
                return {
                    "message": "Upload file successfully assigned to node input",
                    "file_id": file_id,
                }
            logger.error(
                "Workflow input update failed: %s %s", resp.status_code, resp.text
            )
            return {
                "error": "Request failed",
                "status_code": resp.status_code,
                "response": resp.text,
            }
