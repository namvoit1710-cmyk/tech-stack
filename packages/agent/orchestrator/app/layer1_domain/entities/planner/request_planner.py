"""Request planner for decomposing user requests into task-first execution plans."""

from typing import Any


from app.layer1_domain.interfaces.llm_provider_interface import ILLMProvider
from app.layer1_domain.interfaces.logger_interface import ILogger
from app.layer1_domain.entities.planner.task_breakdown_response import OrchestrationPlan, StrategicWorkflowPlan
from app.layer1_domain.entities.planner.task_breakdown_response import TaskBreakdownResponse
from app.layer1_domain.entities.exceptions import QuotaExceededError

class RequestPlanner:
    strategy: StrategicWorkflowPlan | None = None
    plan: TaskBreakdownResponse | None = None


    def create_strategy(self,
        strategy_prompt: Any,
        llm_provider: ILLMProvider,
        logger: ILogger
    ) -> StrategicWorkflowPlan:
        try:
            self.strategy = llm_provider.complete_with_format(
                messages=strategy_prompt,
                response_model=StrategicWorkflowPlan,
            )
        except Exception as e:
            if "insufficient_quota" in str(e):
                raise QuotaExceededError()
            raise ValueError(str(e))

        logger.info("Received strategy classification", strategy=self.strategy)

        if self.strategy is None:
            raise ValueError("LLM provider returned no strategy")
        return self.strategy


    def create_plan_from_strategy(self,
        message: str,
        plan_prompt: Any,
        llm_provider: ILLMProvider,
        logger: ILogger
    ) -> TaskBreakdownResponse:
        if not self.strategy:
            logger.warning("No strategy available to create plan.")
            return TaskBreakdownResponse(
                tasks=[],
                reasoning="No strategy available to create plan.",
            )

        try:
            self.plan = llm_provider.complete_with_format(
                messages=plan_prompt,
                response_model=TaskBreakdownResponse,
            )
            logger.info("Received task breakdown classification", plan=self.plan)
        except Exception as e:
            if "insufficient_quota" in str(e):
                raise QuotaExceededError()
            raise ValueError(str(e))

        if self.plan is None:
            raise ValueError("LLM provider returned no plan")
        return self.plan


    def create_orchestration_plan(self,
        plan_prompt: list[dict[str, str]],
        llm_provider: ILLMProvider,
        logger: ILogger,
    ) -> OrchestrationPlan:
        """Single-call LLM plan: routing + task breakdown + conversation title in one response."""
        try:
            result: OrchestrationPlan = llm_provider.complete_with_format(
                messages=plan_prompt,
                response_model=OrchestrationPlan,
            )
        except Exception as e:
            if "insufficient_quota" in str(e):
                raise QuotaExceededError()
            raise ValueError(str(e))

        if result is None:
            raise ValueError("LLM provider returned no orchestration plan")

        logger.info(
            "Received orchestration plan",
            status=result.status,
            title=result.conversation_title,
        )
        return result
