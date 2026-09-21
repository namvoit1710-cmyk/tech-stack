from typing import Protocol, runtime_checkable

from app.layer1_domain.entities.execution_plan import ExecutionPlan


@runtime_checkable
class IPlannerPort(Protocol):
    """Turns a natural-language prompt into an executable :class:`ExecutionPlan`.

    The use case depends only on this port; the concrete planner (LLM-backed)
    lives in layer4.
    """

    async def plan(self, user_prompt: str) -> ExecutionPlan: ...
