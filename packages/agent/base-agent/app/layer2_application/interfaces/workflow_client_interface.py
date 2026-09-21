from typing import Any, Protocol

from app.layer1_domain.entities.queue import QueueDefinition
from app.layer1_domain.entities.workflow import WorkflowDefinition


class IWorkflowClient(Protocol):
    async def get_workflow_info(self, workflow_id: str) -> dict[str, Any]:
        ...

    async def trigger_workflow(self, workflow_id: str) -> dict[str, Any]:
        ...
