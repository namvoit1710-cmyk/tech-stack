import json
from typing import Optional

from dataclasses import asdict

from agent_sdk.layer4_frameworks.ai.openai_service import LangChainLLMService

from app.layer2_application.interfaces.workflow_input_node_interface import (
    IWorkflowInputNode,
)
from app.layer1_domain.entities.planning_context import PatchInputNodePlanningContext
from app.layer1_domain.entities.planner import PatchInputNodeResult
from app.layer4_frameworks.planners.patch_input_parser import PatchInputNodeParser


class WorkflowInputNode(IWorkflowInputNode):

    def __init__(self, llm: LangChainLLMService):
        self.llm = llm

    async def generate(
        self,
        planning_context: PatchInputNodePlanningContext,
    ) -> PatchInputNodeResult:

        content: str = await self.llm.get_chat_completion(
            self._build_system_prompt(planning_context), "", json_mode=True
        )

        parser = PatchInputNodeParser()
        return parser._parse_response(content)

    # -----------------------------
    # SYSTEM PROMPT
    # -----------------------------
    def _build_system_prompt(
        self, planning_context: PatchInputNodePlanningContext
    ) -> str:
        conversation_context_str = planning_context.global_context.model_dump_json()

        executions_str = f"""
        This is the list of previous tasks/executions in the conversation.

        Important fields:
        - input_data: original input sent to that task, may contain file_id, content_type, filename, or user-provided values
        - output_data: result returned by that task, may contain file summary, extracted fields, raw agent output

        DATA:
        {json.dumps(
            [task.__dict__ for task in (planning_context.executions or [])],
            default=str,
            ensure_ascii=False,
        )}
        """
        fields_text = []

        for f in planning_context.schema:
            cfg = f.fieldConfig
            wrapper = cfg.wrapperProps
            control = cfg.controlProps

            fields_text.append(f"""
- key: {f.key}
  label: {wrapper.label}
  description: {wrapper.description}
  placeholder: {control.placeholder}
  required: {wrapper.required}
  options: {control.options if control.options else 'N/A'}
""")

        # -----------------------------
        # PREVIOUS CONTEXT
        # -----------------------------
        has_previous_result = planning_context.old_patch_input_node_result is not None

        previous_context = ""

        if has_previous_result:
            previous_context = f"""
-----------------------------
PREVIOUS key that have filled and missing filed
-----------------------------

{json.dumps(
    planning_context.old_patch_input_node_result.__dict__,
    indent=2
)}
"""

        # -----------------------------
        # OUTPUT FORMAT
        # -----------------------------
        output_format = []

        # fill
        output_format.append("""
### If mode = "fill":
{
  "mode": "fill",
  "filled_fields": {
    "key": "value"
  }
}
""")

        # partial_no_rag
        output_format.append("""
### If mode = "partial_no_rag":
{
  "mode": "partial_no_rag",
  "filled_fields": {
    "key": "value"
  },
  "missing_fields": ["key3", "key4"],
  "reason": "why remaining fields are not filled and RAG is not needed"
}
""")

        # rag_request only on first run
        if not has_previous_result:
            output_format.append("""
### If mode = "rag_request":
{
  "mode": "rag_request",
  "filled_fields": {
    "key": "value"
  },
  "missing_fields": ["key3", "key4"],
  "required_context": "what additional information is needed",
  "suggesion_rag_query": "suggested query to retrieve relevant documents",
  "document_ids": ["document-xxx", "file_xxx"],
  "reason": "why current context is insufficient",
  "knowledge_base_ids": ["knowledge_base-xxx", "knowledge_base-yyy"]
}
""")

        rag_candidates_block = ""

        if planning_context.rag_candidates:
            rag_candidates_block = f"""
            When RAG data is presented, you MUST use it to fill fields if relevant.
        -----------------------------
        RAG CANDIDATES
        -----------------------------

        {json.dumps(
    [asdict(candidate) for candidate in planning_context.rag_candidates],
    indent=2,
    ensure_ascii=False,
)}
        """

        # -----------------------------
        # FINAL PROMPT
        # -----------------------------
        return f"""
You are a form-filling assistant.

TASK:
Fill schema fields using user input, instructions, and conversation context.

-----------------------------
RULES
-----------------------------
- Use ONLY provided information
- Use conversation context when helpful
- Never hallucinate or guess required values
- Fill as many fields as possible
- Always return valid JSON only (no markdown)

-----------------------------
MODE SELECTION
-----------------------------
You MUST choose exactly ONE mode:
1) fill → all required fields completed
2) partial_no_rag → partial completion, no external retrieval needed
3) rag_request → MUST be used when required information is missing or uncertain and cannot be safely filled from provided context.

-----------------------------
{previous_context}

{rag_candidates_block}

-----------------------------
OUTPUT FORMAT
-----------------------------
You MUST return ONE JSON object ONLY.

{''.join(output_format)}

-----------------------------
INPUTS
-----------------------------

CONVERSATION CONTEXT:
{conversation_context_str}

EXECUTIONS CONTEXT:
{executions_str}

INSTRUCTION:
{planning_context.instruction}

FIELDS:
{''.join(fields_text)}

-----------------------------
CRITICAL RULES
-----------------------------
- ALWAYS include filled_fields
- NEVER overwrite previous results
- NEVER hallucinate missing values
- NEVER return null if inferable
"""
