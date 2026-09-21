from app.layer2_application.interfaces.plan_generator_interface import (
    IPlanGenerator,
)
from app.layer1_domain.entities.planning_context import (
    SummaryContext,
)
from app.layer4_frameworks.planners.planner_parser import PlannerResponse, PlannerParser
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService


class LLMSummaryContextGenerator(IPlanGenerator):

    def __init__(self, llm: LangChainLLMService):
        self.llm = llm

    async def generate(
        self,
        planning_context: SummaryContext,
    ) -> PlannerResponse:
        content: str = await self.llm.get_chat_completion(
            self._build_system_prompt(planning_context), "", json_mode=True
        )
        parser = PlannerParser()
        return parser._parse_response(content)

    # -----------------------------
    # SYSTEM PROMPT (ALL LOGIC)
    # -----------------------------
    def _build_system_prompt(self, planning_context: SummaryContext) -> str:
        # workflows_hub: list[WorkflowHubDTO] = planning_context.workflows_hub
        context: str = planning_context.context

        return f"""
You are a data summarization assistant.

Your task is to summarize the given context into a clear, human-readable explanation.

The input may include:
- Raw data (possibly including headers)
- First few lines of a dataset (e.g., first 10 rows)
- System or workflow-related context

Your goals:
- Understand and describe what the data represents.
- Include a brief explanation of any headers or fields.
- Summarize the key structure and meaning of the data.
- Highlight notable patterns, if visible in the sample.
- Do NOT simply repeat the rows; instead, describe them.
- Keep the summary concise and easy to understand for a non-technical user.
- Avoid jargon unless explained in simple terms.
- Focus on what the user can learn from this sample data.
- Around 200-300 words is ideal for the summary.

Input:
- Context: {context}

Output JSON only:
{{
  "type": "summary",
  "answer": "<summary for enrich llm context>",
  "steps": []
}}

"""
