from app.layer2_application.interfaces.review_generator_interface import IReviewGenerator
from app.layer2_application.interfaces.reviewer_resolver_interface import (
    IReviewerResolver,
)
from app.layer1_domain.entities.review_context import ReviewContextType
from app.layer4_frameworks.reviewer.llm_review_generator import (
    LLMReviewGenerator,
)
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService

class ReviewerResolver(IReviewerResolver):
    def __init__(self, llm=LangChainLLMService):
        self.llm = llm

    def resolve(self, planning_context_type: ReviewContextType) -> IReviewGenerator:
        if planning_context_type == ReviewContextType.LLM:
            if not self.llm:
                raise ValueError("No OpenAI service provided for LLM planner")

            return LLMReviewGenerator(self.llm)
        else:
            raise ValueError(f"No planner registered for {planning_context_type}")