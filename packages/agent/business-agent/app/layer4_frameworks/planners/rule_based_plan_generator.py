from typing import Any
from app.layer2_application.interfaces.plan_generator_interface import IPlanGenerator
from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.planner import PlannerResponse
class RuleBasedPlanGenerator(IPlanGenerator):

    async def generate(
        self,
        orches_plan: Any,
        business_intent: str,
        tools: list,
    ) -> PlannerResponse:

        # Example: simple rule-based planning logic

        steps = []

        # Rule 1: if tools exist, map 1 tool per step
        for tool in tools:
            steps.append({
                "tool": tool["name"],
                "input": {
                    "agent": orches_plan.execute_by,
                    "intent": business_intent,
                }
            })

        # Rule 2: fallback if no tools
        if not steps:
            steps.append({
                "tool": "noop",
                "input": {
                    "message": "No tools available"
                }
            })

        return None