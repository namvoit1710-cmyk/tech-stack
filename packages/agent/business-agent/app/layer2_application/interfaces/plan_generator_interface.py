from app.layer1_domain.entities.planning_context import BasePlanningContext
from app.layer1_domain.entities.planner import PlannerResponse
from typing import Protocol, TypeVar
TContext = TypeVar("TContext", bound=BasePlanningContext)

class IPlanGenerator(Protocol[TContext]):

    async def generate(
        self,
        planning_context: TContext,
    ) -> PlannerResponse:
        ...
