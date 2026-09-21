from app.layer2_application.interfaces.plan_generator_interface import IPlanGenerator
from app.layer2_application.interfaces.planner_resolver_interface import (
    IPlannerResolver,
)
from app.layer1_domain.entities.planning_context import PlanningContextType
from app.layer4_frameworks.planners.llm_plan_generator import (
    LLMPlanGenerator,
)
from app.layer4_frameworks.planners.llm_plan_simple_node_generator import (
    LLMPlanSimpleNodeGenerator,    
)
from app.layer4_frameworks.planners.llm_plan_sub_user_chat_generator import (
    LLMPlanSubUserChatGenerator,
)
from app.layer4_frameworks.planners.llm_summary_context import (
    LLMSummaryContextGenerator,
)
from app.layer4_frameworks.planners.llm_execution_summary import (
    LLMExecutionSummaryGenerator,
)
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService

class PlannerResolver(IPlannerResolver):
    def __init__(self, llm: LangChainLLMService):
        self.llm = llm

    def resolve(self, planning_context_type: PlanningContextType) -> IPlanGenerator:
        if not self.llm:
            raise ValueError("No LLM provided for LLM planner")

        if planning_context_type == PlanningContextType.LLM:
            return LLMPlanGenerator(self.llm)
        elif planning_context_type == PlanningContextType.LLM_WITH_SUB_USER_CHAT:
            return LLMPlanSubUserChatGenerator(self.llm)
        elif planning_context_type == PlanningContextType.LLM_WITH_SIMPLE_NODE:
            return LLMPlanSimpleNodeGenerator(self.llm)
        elif planning_context_type == PlanningContextType.RULE:
            raise ValueError("No rule planner provided")
        elif planning_context_type == PlanningContextType.LLM_SUMMARY_CONTEXT_GENERATOR:
            return LLMSummaryContextGenerator(self.llm)
        elif planning_context_type == PlanningContextType.LLM_EXECUTION_SUMMARY:
            return LLMExecutionSummaryGenerator(self.llm)
        else:
            raise ValueError(f"No planner registered for {planning_context_type}")
