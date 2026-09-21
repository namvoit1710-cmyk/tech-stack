from __future__ import annotations

import json
import re
from typing import Any

from app.layer2_application.interfaces.workflow_repairer_interface import (
    IWorkflowRepairer,
    WorkflowRepairError,
)

_REPAIR_SYSTEM_PROMPT = """You are repairing a workflow definition that failed validation.

Given the CURRENT definition (name, description, nodes, edges) and the error it produced,
return a corrected definition as a SINGLE JSON object with exactly these keys: "name",
"description", "nodes", "edges". Each node is {"id","type","name","config", optional "x","y"}.
Each edge is {"source","target"} referencing node ids. Make the SMALLEST change that fixes
the reported error — do not redesign the workflow. Return ONLY the JSON object: no prose, no
markdown code fences."""

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_REQUIRED_KEYS = ("name", "nodes", "edges")


class LlmWorkflowRepairer(IWorkflowRepairer):
    """One-shot LLM fix: given a failing definition + error, returns a corrected definition
    PAYLOAD. Never edits workflow code and never touches the runtime execution engine — the
    caller (``ValidateRepairGate``) re-persists the correction via the normal upsert."""

    def __init__(self, llm_service: Any) -> None:
        self._llm = llm_service

    async def repair(self, definition: dict, error_message: str, error_details: dict) -> dict:
        user_prompt = json.dumps({
            "definition": definition,
            "error": error_message,
            "error_details": error_details or {},
        }, default=str)
        try:
            raw = await self._llm.get_chat_completion(_REPAIR_SYSTEM_PROMPT, user_prompt, json_mode=True)
        except Exception as exc:  # LLM call itself failed
            raise WorkflowRepairError(f"repair LLM call failed: {exc}") from exc
        corrected = self._parse_definition(raw)
        return corrected

    @classmethod
    def _parse_definition(cls, raw: str) -> dict:
        candidate = (raw or "").strip()
        fenced = _JSON_FENCE_RE.search(candidate)
        if fenced:
            candidate = fenced.group(1).strip()
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise WorkflowRepairError(f"repair LLM response is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict) or not all(key in parsed for key in _REQUIRED_KEYS):
            raise WorkflowRepairError(
                "repair LLM response is missing a corrected workflow definition"
            )
        return parsed
