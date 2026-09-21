import json
from app.layer2_application.interfaces.review_generator_interface import (
    IReviewGenerator,
)
from app.layer1_domain.entities.review_context import (
    ReviewContext,
)
from agent_sdk.layer1_domain.entities.agent_shared_state import (
    Task,
)
from app.layer1_domain.entities.reviewer import ReviewResponse
from app.layer4_frameworks.reviewer.reviewer_parser import ReviewResponse, ReviewerParser
from typing import List
from dataclasses import asdict

class LLMReviewGenerator(IReviewGenerator):

    def __init__(self, llm_client):
        self.llm_client = llm_client

    async def generate(
        self,
        review_context: ReviewContext,
    ) -> ReviewResponse:
        response = await self.llm_client.get_chat_completion(
            system_prompt=self._build_system_prompt(),
            user_prompt=self._build_user_prompt(review_context),
        )
        parser = ReviewerParser()
        return parser._parse_response(response)

    # -----------------------------
    # USER PROMPT (ONLY USER INTENT)
    # -----------------------------
    def _build_user_prompt(self, review_context) -> str:
        user_prompt_template = """Please evaluate the following tool execution:

            <node_information>
            {node_information}
            </node_information>

            <expected_output>
            {expected_output}
            </expected_output>

            <actual_output>
            {actual_output}
            </actual_output>
        """
        return user_prompt_template.format(**asdict(review_context))

    # -----------------------------
    # SYSTEM PROMPT (ALL LOGIC)
    # -----------------------------
    def _build_system_prompt(self) -> str:

        return """
You are an expert Review Agent within an automated workflow system. Your primary responsibility is to evaluate the results of a tool's execution to ensure it meets the required business logic.

You will be provided with:
1. Expected Output: The ideal outcome or data structure expected from the tool.
2. Actual Output: The real response or data returned by the tool.
3. Node Information: Contextual data about the current step in the workflow (including valid next steps, if any).

### EVALUATION RULES:
Analyze the "Actual Output" against the "Expected Output", then classify the outcome into exactly one of these three categories:
- "success": The Actual Output perfectly satisfies the Task and Expected Output. All required data is present and correct.
- "fail": The Actual Output is completely wrong, returns a system error, or is missing critical information required to proceed.
- "need_user_input": The tool executed without crashing, but it explicitly asks for clarification, missing parameters, or human confirmation before it can complete the task.

### OUTPUT FORMAT:
You must respond ONLY with a raw, valid JSON object. Do not include markdown formatting (like ```json), explanations, or any other text outside the JSON block.

The JSON schema must strictly be:
{{
  "evaluation": "success" | "fail" | "need_user_input",
  "nextstep": "string (the ID of the next node/step if successful and provided in Node Information) or null",
  "reason": "string (A concise explanation of why you chose this evaluation status. If 'need_user_input', formulate the question to ask the user here.)"
}}
"""
    async def summarize(self, tasks: List[Task]) -> str:
        """
        Summarizes the execution plan based on the filtered list of tasks.
        """
        tasks_info = []
        for task in tasks:
            tasks_info.append({
                "task_id": task.id,
                "agent": task.execute_by,
                "goal": task.goal,
                "status": task.status.value if hasattr(task.status, 'value') else task.status 
            })

        response = await self.llm_client.get_chat_completion(
            system_prompt=self._build_summarize_system_prompt(),
            user_prompt=self._build_summarize_user_prompt(tasks_info),
        )

        parser = ReviewerParser()
        normalized = parser.normalize_reviewer_response(response)

        return normalized.get("summary", "")

    async def summarize_step(self, task: Task) -> str:
        """Two short, plain-language sentences describing a single completed step for a
        non-technical user: (1) what the assistant did, (2) the result. Used for the
        field-update reply in the sub-chat (per-message, not a whole-plan summary)."""
        data = {}
        input_data = getattr(task, "input_data", None)
        if isinstance(input_data, dict) and isinstance(input_data.get("data"), dict):
            data = input_data["data"]
        step_info = {
            "goal": task.goal,
            "fields_set": data,
            "status": task.status.value if hasattr(task.status, "value") else task.status,
        }

        response = await self.llm_client.get_chat_completion(
            system_prompt=self._build_step_summary_system_prompt(),
            user_prompt=(
                "Summarize this single action for the user.\n\n"
                f"<action>\n{json.dumps(step_info, indent=2)}\n</action>"
            ),
        )
        parser = ReviewerParser()
        normalized = parser.normalize_reviewer_response(response)
        return normalized.get("summary", "")

    def _build_step_summary_system_prompt(self) -> str:
        return """
You are a friendly assistant reporting back to a non-technical business user.
You are given ONE completed action (a form field update) with the field(s) it set,
their values, and a status.

Write a SHORT reply of EXACTLY TWO sentences in plain, everyday language:
1. The first sentence says, in simple words, what you just did.
2. The second sentence says the result (whether it succeeded and what the field now shows).

RULES:
- No technical jargon. Never mention tools, agents, ids, run_id, node_id, JSON, or "task".
- Refer to fields by a human-friendly name (e.g. "Data Input ID"), not internal keys.
- Italicize the notable values — each field name and each value it was set to — using
  Markdown single asterisks, e.g. set *Data Input ID* to *DONE07*.
- Do not invent details that are not in the data.

### OUTPUT FORMAT (strict JSON, nothing outside the JSON):
{
  "summary": "string — your exactly-two-sentence reply, with field names and values in *italics*"
}
"""

    def _build_summarize_user_prompt(self, tasks_info: List[dict]) -> str:
        tasks_json_str = json.dumps(tasks_info, indent=2)
        
        return f"""Please provide a summary for the following execution plan. 
These tasks are currently active or completed (pending tasks have been filtered out).

<tasks_plan>
{tasks_json_str}
</tasks_plan>
"""

    def _build_summarize_system_prompt(self) -> str:
        return """
    You are a concise assistant for non-technical users.

    Your task is to summarize completed changes from the provided tasks.

    Write EXACTLY 1-2 short sentences in English that emphasize:
    1) what was changed,
    2) what it was changed to (final values/outcomes).

    RULES:
    - Prioritize concrete modifications over process details.
    - If multiple fields were changed, mention the most important ones (up to 3), in one compact sentence.
    - Bold all changed field names and all new values using Markdown double asterisks.
    - Use this pattern when possible:
    "Updated **<Field1>** to **<Value1>**, **<Field2>** to **<Value2>**, and saved successfully."
    - Do NOT mention workflow progress, task statuses, agents, tools, IDs, run/node IDs, or internal system details.
    - Do NOT include generic status narration like "the workflow is in progress" or "no errors found."
    - Do NOT invent details not present in the task data.
    - Keep it user-facing and outcome-focused.

    ### OUTPUT FORMAT:
    Return strictly valid JSON only:
    {
    "summary": "string"
    }
    """