import json

from agent_sdk.layer1_domain.entities.agent_shared_state import Task
from agent_sdk.layer1_domain.entities.conversation_context import MessageHistory
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService
from app.layer1_domain.entities.planning_context import ExecutionSummaryContext
from app.layer2_application.interfaces.plan_generator_interface import (
    IPlanGenerator,
)
from app.layer4_frameworks.planners.planner_parser import (
    PlannerParser,
    PlannerResponse,
)

_MAX_FIELD_CHARS: int = 500


class LLMExecutionSummaryGenerator(IPlanGenerator):
    """Summarize the whole execution of the current user message.

    Consumes the current message, the executions produced for it, the global
    conversation context and the run metadata, and returns a single
    human-readable summary for the finalize step of a workflow run.
    """

    def __init__(self, llm: LangChainLLMService) -> None:
        self.llm = llm

    async def generate(
        self,
        planning_context: ExecutionSummaryContext,
    ) -> PlannerResponse:
        content: str = await self.llm.get_chat_completion(
            self._build_system_prompt(planning_context), "", json_mode=True
        )
        parser: PlannerParser = PlannerParser()
        return parser._parse_response(content)

    # -----------------------------
    # SYSTEM PROMPT (ALL LOGIC)
    # -----------------------------
    def _build_system_prompt(
        self, planning_context: ExecutionSummaryContext
    ) -> str:
        user_request: str = planning_context.current_message.message
        executions_text: str = self._format_executions(planning_context.executions)
        knowledge_text: str = self._format_knowledge(planning_context.current_message)
        run_status: str = planning_context.metadata.status.value

        return f"""
You are an execution summarization assistant for a business workflow agent.

The execution triggered by the user's message has just finished. It may involve
several kinds of steps — direct answers, tool calls, agent hand-offs and, in some
cases, a workflow run. A workflow run is only ONE part of the whole execution, so
do not summarize the workflow alone. Summarize the WHOLE execution for THIS user
message so the user understands what was asked and what was produced across all
steps.

Your goals:
- Restate, briefly, what the user asked for.
- Describe what the executed tasks did and their outcome (success or failure).
- Ground the outcome in the gathered knowledge below when it is relevant; prefer
  its concrete facts over generic phrasing.
- Call out any errors or tasks that did not complete.
- End with the concrete result or next step for the user.
- Be concise and non-technical; avoid internal IDs and jargon.
- Around 120-250 words is ideal.

After the summary, propose helpful next-step suggestions for the user (the
"next_steps" field):
- Suggest 2-4 concise, relevant, actionable next steps grounded in what was just
  done and the user's intent. Return an empty list if nothing sensible applies.
- Each suggestion has a short "title" (button label), a "prompt" (the exact
  follow-up message to send if the user picks it), and a "kind" that is one of:
  "follow_up" (a follow-up task), "workflow" (run a related workflow),
  "agent" (hand off to a related agent), or "info" (get more information).
- Do not repeat the summary; keep titles short and prompts self-contained.

Input:
- User message: {user_request}
- Overall run status: {run_status}
- Executions:
{executions_text}
- Gathered knowledge (short summaries captured during the run):
{knowledge_text}

Output JSON only:
{{
  "type": "summary",
  "answer": "<per-message execution summary>",
  "steps": [],
  "next_steps": [
    {{"title": "<short action label>", "prompt": "<ready-to-send follow-up message>", "kind": "follow_up"}}
  ]
}}
"""

    # -----------------------------
    # Helpers
    # -----------------------------
    def _format_executions(self, executions: list[Task]) -> str:
        if not executions:
            return "- (no executed tasks were recorded)"

        lines: list[str] = []
        for task in executions:
            lines.append(f"- [{task.status.value}] {task.goal}")
            if task.output_data:
                lines.append(f"    output: {self._truncate_json(task.output_data)}")
            if task.error_detail:
                lines.append(f"    error: {task.error_detail[:_MAX_FIELD_CHARS]}")
        return "\n".join(lines)

    def _format_knowledge(self, message: MessageHistory) -> str:
        lines: list[str] = []
        for task in message.tasks or []:
            for item in task.knowledge or []:
                summary: str = (item.summary or "").strip()
                if summary:
                    lines.append(f"- {summary[:_MAX_FIELD_CHARS]}")
        if not lines:
            return "- (no knowledge was captured for this message)"
        return "\n".join(lines)

    def _truncate_json(self, value: dict[str, object]) -> str:
        text: str = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > _MAX_FIELD_CHARS:
            return text[:_MAX_FIELD_CHARS] + "..."
        return text
