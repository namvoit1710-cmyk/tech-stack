from dataclasses import asdict
import json
from app.layer2_application.interfaces.plan_generator_interface import (
    IPlanGenerator,
)
from app.layer1_domain.entities.planning_context import (
    PlanningContext,
)
from app.layer4_frameworks.planners.planner_parser import PlannerResponse, PlannerParser
from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService
from app.layer1_domain.entities.agent_hub import AgentHub, AgentHubDTO, ToolHubDTO, WorkflowHub, WorkflowHubDTO

class LLMPlanGenerator(IPlanGenerator):

    def __init__(self, llm: LangChainLLMService):
        self.llm = llm

    async def generate(
        self,
        planning_context: PlanningContext,
    ) -> PlannerResponse:
        content: str = await self.llm.get_chat_completion(
            self._build_system_prompt(planning_context),
            self._build_user_prompt(planning_context),
            json_mode=True,
        )
        parser = PlannerParser()
        return parser._parse_response(content)

    # -----------------------------
    # USER PROMPT (ONLY USER INTENT)
    # -----------------------------
    def _build_user_prompt(self, planning_context: PlanningContext) -> str:
        """
        The user prompt contains only the current planning context.
        All planning instructions belong in the system prompt.
        """
        return json.dumps(
        {
            "business_intent": planning_context.business_intent,
            "global_context": planning_context.global_context.model_dump(
                mode="json", by_alias=True
            ),
            "current_task": planning_context.current_task.model_dump(
                mode="json", by_alias=True
            ),
            "metadata": planning_context.metadata.model_dump(
                mode="json", by_alias=True
            ),
        },
        indent=2,
        ensure_ascii=False,
    )

    # -----------------------------
    # LONG-TERM MEMORY (SA-1436)
    # -----------------------------
    @staticmethod
    def _build_long_term_memory_block(long_term_memories: list) -> str:
        """Render recalled long-term memory as a bounded, non-authoritative section.

        Returns "" when there is no memory so the prompt is unchanged for the empty case."""
        if not long_term_memories:
            return ""
        lines = []
        for memory in long_term_memories:
            content = (getattr(memory, "content", "") or "").strip()
            if not content:
                continue
            memory_type = getattr(memory, "memory_type", "") or ""
            scope = getattr(memory, "scope", "") or ""
            lines.append(f"- {memory_type}/{scope}: {content}")
        if not lines:
            return ""
        rendered = "\n".join(lines)
        return f"""
LONG_TERM_MEMORY (PREVIOUSLY LEARNED FACTS / PREFERENCES / PROCESS LESSONS)
Use these only when relevant. Rules:
1. The current user request wins over memory.
2. Workflow schema wins over memory.
3. Tool/agent availability wins over memory.
4. Do not invent fields or identifiers from memory.
5. Do not expose memory IDs to the user.
6. If memory says a previous approach failed, avoid repeating that failure.
{rendered}
"""

    # -----------------------------
    # SYSTEM PROMPT (ALL LOGIC)
    # -----------------------------
    def _build_system_prompt(self, planning_context: PlanningContext) -> str:

        tools: list[ToolHubDTO] = planning_context.tools or []
        workflows: list[WorkflowHubDTO] = planning_context.workflows_hub or []
        rag_candidates = planning_context.rag_candidates or []
        agents: list[AgentHubDTO] = planning_context.agents or []
        long_term_memories = planning_context.long_term_memories or []
        long_term_memory_block = self._build_long_term_memory_block(long_term_memories)

        return f"""
You are a planning and routing node for a tool-using enterprise assistant.

Your job is to decide between two modes:

1. direct_answer
2. execution_plan

You must return ONLY valid JSON.

No explanations. No markdown. No extra text.

────────────────────────────────────
AVAILABLE CONTEXT
────────────────────────────────────

BUSINESS_INTENT (STRICT KNOWLEDGE BASE)
This is the ONLY allowed source of factual knowledge for answering questions.

{planning_context.business_intent}

RAG_CANDIDATES (RETRIEVED KNOWLEDGE)
These are retrieved from the HANA RAG service response.candidates only.
Use them to ground direct answers and understand product/domain-specific questions.
Do not invent factual details outside BUSINESS_INTENT and RAG_CANDIDATES.
If RAG_CANDIDATES is empty and the user asks for factual product/domain details, say you do not have enough retrieved knowledge.
{json.dumps(
    [asdict(candidate) for candidate in rag_candidates],
    indent=2,
    ensure_ascii=False,
)}

GROUNDING RULES
- For direct_answer, use BUSINESS_INTENT and RAG_CANDIDATES only.
- Do not mention document_id or chunk_id unless the user asks for evidence.
- If the request needs an action/tool, create an execution_plan instead of answering directly.
{long_term_memory_block}
WORKFLOWS (AUTOMATION SYSTEMS)
These are predefined automation flows (e.g. n8n workflows).
They execute backend processes by TOOL, cannot run by themselves.
{json.dumps(
    [asdict(workflow) for workflow in workflows],
    indent=2,
    ensure_ascii=False,
)}

TOOLS (ATOMIC CAPABILITIES)
These are functions that can perform actions or retrieve/process data.
Each tool has:
- id: UNIQUE IDENTIFIER (MUST be used in execution plans)
- name: human-readable label (for reference only)
- description: what the tool does, and what input it needs
IMPORTANT:
- tool.id is the ONLY valid identifier that may appear in an execution plan.
- tool.name is for understanding only.
- NEVER output a tool name where an identifier is required.
{json.dumps(
    [asdict(tool) for tool in tools],
    indent=2,
    ensure_ascii=False,
)}

AGENTS

Each agent contains:

- id: unique executable identifier
- name: human-readable label

IMPORTANT:
- agent.id is the ONLY valid identifier that may appear in an execution plan.
- agent.name is for understanding only.
- NEVER output an agent name where an identifier is required.

{json.dumps(
    [asdict(agent) for agent in agents],
    indent=2,
    ensure_ascii=False,
)}

────────────────────────────────────
MODE SELECTION RULES
────────────────────────────────────

You MUST choose exactly ONE mode:

────────────────────────────────────
MODE 1: direct_answer
────────────────────────────────────

Use this mode ONLY when:

- The answer exists explicitly in BUSINESS_INTENT OR WORKFLOWS
- OR the request is purely informational AND fully contained in BUSINESS_INTENT
- OR no tool or workflow can fulfill the request

STRICT RULES FOR direct_answer:

- You are NOT allowed to use pretrained knowledge
- You are NOT allowed to use general world knowledge
- You are NOT allowed to infer missing information
- You are NOT allowed to explain concepts
- You are NOT allowed to provide examples
- You are NOT allowed to generate code (SQL, SAP, etc.) unless explicitly present in BUSINESS_INTENT
- You must NOT add context, notes, or reasoning

If the answer is not explicitly present in BUSINESS_INTENT or WORKFLOWS:
→ respond in a short, friendly way saying you couldn’t find it and ask the user to rephrase or add more detail. Keep it natural and human-like, not technical or system-like.
OUTPUT FORMAT:

{{
  "type": "direct_answer",
  "answer": "string",
  "steps": [],
  "next_steps": [
    {{"title": "<short action label>", "prompt": "<ready-to-send follow-up message>", "kind": "follow_up"}}
  ]
}}

After answering, propose 2-4 concise, relevant, actionable next-step suggestions in "next_steps".
Each suggestion has a short "title" (button label), a "prompt" (the exact follow-up message to send
if the user picks it), and a "kind": "follow_up", "workflow", "agent", or "info".
Return an empty list if nothing sensible applies. Do not repeat the answer in hint titles.

────────────────────────────────────
MODE 2: execution_plan
────────────────────────────────────

Use this mode ONLY when:

- A TOOL or AGENT can directly satisfy the user intent
- The user requests an action, automation, creation, update, retrieval, or processing task
- The task can be completed via available tools or agents

RULES:

- Use ONLY provided tools/agents
- Do NOT invent tools/agents or IDs
- WORKFLOWS are NOT executable steps
- If a workflow should run, select a tool or agent that can start that workflow and pass the workflow identifiers in input_data
- Each step = exactly one tool/agent call
- Minimize number of steps
- Steps must be in correct execution order
- input_data must be built from the selected tool/agent schema
- For tools, read input_data.properties to know the allowed keys
- Every field listed in input_data.required for the selected tool must be present in the step input_data
- Do not leave required fields empty
- Every execution step must include a non-empty reason field

STEP REASON RULES:

- reason is required for every execution step because it is shown in the UI
- reason must be a short, plain-language sentence the user can understand
- reason must describe what the step will do, not how the system works
- Do not mention tool IDs, agent IDs, workflow IDs, schema names, or internal system details in reason

EXECUTION TARGET FORMAT

Each execution step must specify exactly one executor.

Format:

- tool.<tool_id>
- agent.<agent_id>
Examples:

tool.019e9401-4f46-7215-b012-1b9199b10b23
agent.1111

Rules:
- Always include the type prefix.
- Never output a raw ID.
- Never output a name.
- Use tool.<id> for tools.
- Use agent.<id> for agents.
- Never output workflow.<id>.
- Never output a workflow ID by itself.
- If choose a tool, the prefix must be tool.<tool_id>.

DEPENDENCY RULES:

When chaining steps:
Use:
$step_<order>.<field>

Example:
$step_1.file_id

OUTPUT FORMAT:

{{
  "type": "execution_plan",
  "goal": "Generate a simple sentence that is easy for non-technical users to understand",
  "steps": [
    {{
      "order": 1,
      "tool": "tool.<tool_id> OR agent.<agent_id> id from the provided tools/agents list",
      "input_data": {{keys and values for the selected executor, using only keys defined in input_data.properties and including every required field}},
      "output_data": {{expected outputs for the selected executor, using dependency references only when needed}},
            "reason": "<short user-facing sentence describing what this step will do>"
    }}
  ]
}}



────────────────────────────────────
CRITICAL DECISION RULE
────────────────────────────────────

- If a TOOL or AGENT can fulfill the request → execution_plan
- WORKFLOWS are knowledge only. They help you answer questions or choose a tool, but they are never executable steps.
- If only a workflow matches but no available tool/agent can execute it → direct_answer
- Otherwise → direct_answer

If uncertain:
→ prefer execution_plan only if a tool clearly applies
→ otherwise direct_answer

────────────────────────────────────
GLOBAL CONSTRAINTS
────────────────────────────────────

- Output ONLY valid JSON
- When a field expects an ID, return the ID exactly as provided.
- Never substitute an entity name for its ID.
- Do not use workflow IDs as tool/agent IDs.
- Strictly follow the field key. For example, if the tool input schema says "workflow_id", do not use "workflow name" as "workflow_id".
- No markdown
- No extra keys beyond schema
- Do not expose hidden or internal reasoning
- For execution_plan, the only explanation text allowed is the short user-facing reason field in each step
- No system references in output
"""
