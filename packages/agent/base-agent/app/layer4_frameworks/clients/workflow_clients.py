import logging
from typing import Any

import httpx

from app.layer1_domain.entities.queue import QueueDefinition
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer2_application.interfaces.workflow_client_interface import IWorkflowClient

logger = logging.getLogger(__name__)

# TODO: Implement logic of workflow API
class WorkflowClient(IWorkflowClient):
    def __init__(self, base_url: str = ""):
        self._base_url = base_url.rstrip("/")


    async def get_workflow_info(self, workflow_id: str) -> dict[str, Any]:
        if not self._base_url:
            return {"status": "mock", "workflow_id": workflow_id}
        
        url = f"{self._base_url}/workflows/{workflow_id}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("Failed to fetch workflow info for %s: %s", workflow_id, exc)
            return {"workflow_id": workflow_id, "status": "error", "error": str(exc)}


    async def trigger_workflow(self, workflow_id: str) -> dict[str, Any]:
        if not self._base_url:
            return {"status": "mock", "workflow_id": workflow_id}
        
        url = f"{self._base_url}/runs"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    url,
                    json={"workflow_id": workflow_id},
                )
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("Failed to trigger workflow %s: %s", workflow_id, exc)
            return {"status": "error", "error": str(exc)}

