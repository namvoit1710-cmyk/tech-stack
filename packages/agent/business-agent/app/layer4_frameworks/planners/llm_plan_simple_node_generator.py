from dataclasses import asdict
import json
from app.layer2_application.interfaces.plan_generator_interface import (
    IPlanGenerator,
)
from app.layer1_domain.entities.planning_context import (
    SimpleNodePlanningContext,
)
from app.layer4_frameworks.planners.planner_parser import PlannerResponse, PlannerParser
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService
from app.layer1_domain.entities.agent_hub import WorkflowHub, WorkflowHubDTO
from app.layer1_domain.entities.workflow_entity import Workflow

class LLMPlanSimpleNodeGenerator(IPlanGenerator):

    def __init__(self, llm: LangChainLLMService):
        self.llm = llm

    async def generate(
        self,
        planning_context: SimpleNodePlanningContext,
    ) -> PlannerResponse:
        content: str = await self.llm.get_chat_completion(
            self._build_system_prompt(planning_context), "", json_mode=True
        )
        parser = PlannerParser()
        return parser._parse_response(content)

    # -----------------------------
    # SYSTEM PROMPT (ALL LOGIC)
    # -----------------------------
    def _build_system_prompt(self, planning_context: SimpleNodePlanningContext) -> str:
        workflows_hub: list[WorkflowHubDTO] = planning_context.workflows_hub
        node_message: str = planning_context.node_message

        return f"""
You are a workflow assistant.

Your task is to rewrite the current workflow node message into a user-friendly message.

The user is an end user and may not understand technical terms such as:

- workflow
- node
- trigger
- automation
- process ID
- system status
- internal step names

Your goals:

- Use simple, natural, conversational language.
- Clearly explain what happened from the user's perspective.
- Explain why this step matters to the user's goal.
- Clearly state what action the user should take next, if any.
- Encourage the user to continue.
- Preserve the original meaning of the node message.
- Use the workflow description only as background context.
- Do not expose internal implementation details.
- Avoid repeating technical workflow names unless they are meaningful to the user.
- Focus on the user's outcome, not the system's internal behavior.
- If the original message is a status update, translate it into plain language that explains the impact on the user.
- If no user action is required, clearly say so.
- Keep the message concise (1-3 short paragraphs).
- Sound helpful and professional, but do not exaggerate.
- Generate markdown formatted text.
- Friendly but not overly enthusiastic.
- Avoid "Good news", "Great news", and similar phrases.

Input:
- Workflow hub: {json.dumps([asdict(workflow) for workflow in workflows_hub], indent=2, ensure_ascii=False)}
- Current node message: {node_message}

Output JSON only:

{{
  "type": "direct_answer",
  "answer": "<user-facing message written in Markdown>",
  "steps": []
}}


"""
