from typing import Protocol
from app.layer1_domain.entities.planning_context import (
    PatchInputNodePlanningContext,

)
from app.layer1_domain.entities.planner import PatchInputNodeResult

class IWorkflowInputNode(Protocol):
    async def generate(
        self,
        planning_context: PatchInputNodePlanningContext,
    ) -> PatchInputNodeResult:...
