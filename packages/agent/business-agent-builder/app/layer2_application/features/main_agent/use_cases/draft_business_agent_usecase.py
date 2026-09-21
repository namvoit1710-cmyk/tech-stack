"""Use case: draft a full business-agent config from a natural-language prompt.

If an LLM service is wired in (``openai_service``) it is used to infer the fields; otherwise
a deterministic heuristic keeps the agent fully functional (and the suite green) offline.

Layer 2: standard-library + domain only. The LLM is duck-typed (no framework import).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.business_agent_draft import BusinessAgentDraft
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.prompts.prompt import (
    DRAFT_SYSTEM_PROMPT,
    SELECT_TARGETS_SYSTEM_PROMPT,
)

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "for", "to", "that", "can", "with", "and", "of", "in",
        "on", "is", "it", "me", "my", "our", "we", "create", "make", "build",
        "agent", "please", "help", "which", "who", "will", "should",
    }
)

_CAPABILITY_HINTS = {
    "email": "draft_email",
    "outreach": "outreach",
    "faq": "answer_faq",
    "question": "answer_questions",
    "support": "customer_support",
    "customer": "customer_support",
    "summar": "summarize",
    "report": "generate_report",
    "data": "analyze_data",
    "csv": "process_csv",
    "research": "research",
    "content": "create_content",
    "schedule": "schedule",
}


@dataclass
class DraftInputDto:
    prompt: str
    # Registry catalog supplied by the caller (Agent Hub); each item: {id, name, description}.
    # Used to assign real, validated tool/agent IDs to the drafted agent.
    available_tools: list | None = None
    available_agents: list | None = None


@dataclass
class DraftOutputDto:
    draft: BusinessAgentDraft
    source: str  # "llm" or "heuristic"
    is_reviewed: bool = False


class DraftBusinessAgentUseCase:
    def __init__(self, logger: ILogger, openai_service: Any | None = None):
        self._logger = logger
        self._llm = openai_service

    async def execute(self, input_dto: DraftInputDto) -> DraftOutputDto:
        prompt = (input_dto.prompt or "").strip()
        if not prompt:
            raise ValueError("prompt is required to draft a business agent")

        draft: BusinessAgentDraft | None = None
        source = "heuristic"
        if self._llm is not None:
            try:
                fields = await self._draft_with_llm(prompt)
                draft = self._build_draft(prompt, fields)
                source = "llm"
                self._logger.info(f"Drafted business agent via LLM: {draft.name}")
            except Exception as exc:  # noqa: BLE001 - fall back, never fail the chat
                self._logger.warning(
                    f"LLM draft failed ({exc}); falling back to heuristic draft"
                )

        if draft is None:
            draft = self._build_draft(prompt, self._heuristic_fields(prompt))
            self._logger.info(f"Drafted business agent via heuristic: {draft.name}")

        # Assign real, validated tool/agent IDs from the supplied registry catalog.
        await self._assign_from_catalog(draft, prompt, input_dto)
        return DraftOutputDto(draft=draft, source=source)

    # -- Registry catalog assignment -----------------------------------------

    async def _assign_from_catalog(
        self, draft: BusinessAgentDraft, prompt: str, input_dto: DraftInputDto
    ) -> None:
        tools_cat = input_dto.available_tools or []
        agents_cat = input_dto.available_agents or []
        if not tools_cat and not agents_cat:
            return

        # The LLM decides intent (all / none / subset) in ANY language — no brittle,
        # English-only keyword rules. Keyword matching is only an offline fallback.
        tool_ids: list[str] | None = None
        agent_ids: list[str] | None = None
        if self._llm is not None:
            try:
                tool_ids, agent_ids = await self._select_with_llm(prompt, tools_cat, agents_cat)
            except Exception as exc:  # noqa: BLE001 - fall back to keyword match
                self._logger.warning(
                    f"LLM tool/agent selection failed ({exc}); using keyword match"
                )

        need = self._need_tokens(prompt, draft.capabilities)
        if tool_ids is None:
            tool_ids = self._match_catalog(tools_cat, need)
        if agent_ids is None:
            agent_ids = self._match_catalog(agents_cat, need)

        # Every assigned id must exist in the supplied catalog (no hallucinated ids).
        tools = self._validate_ids(tool_ids, tools_cat)
        agents = self._validate_ids(agent_ids, agents_cat)
        if tools:
            draft.tools = tools
        if agents:
            draft.agents = agents
        if tools or agents:
            self._logger.info(
                f"Assigned {len(tools)} tool(s) and {len(agents)} agent(s) from the registry catalog"
            )

    @staticmethod
    def _validate_ids(ids: list[str] | None, catalog: list) -> list[str]:
        if not ids:
            return []
        valid = {
            str(item.get("id")).strip()
            for item in catalog
            if isinstance(item, dict) and item.get("id")
        }
        out: list[str] = []
        for raw in ids:
            candidate = str(raw).strip()
            if candidate in valid and candidate not in out:
                out.append(candidate)
        return out

    async def _select_with_llm(
        self, prompt: str, tools_cat: list, agents_cat: list
    ) -> tuple[list[str], list[str]]:
        def _slim(catalog: list) -> list[dict]:
            return [
                {
                    "id": str(item.get("id")),
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                }
                for item in catalog
                if isinstance(item, dict) and item.get("id")
            ]

        user_payload = json.dumps(
            {
                "request": prompt,
                "AVAILABLE_TOOLS": _slim(tools_cat),
                "AVAILABLE_AGENTS": _slim(agents_cat),
            }
        )
        raw = await self._llm.get_chat_completion(
            SELECT_TARGETS_SYSTEM_PROMPT, user_payload, json_mode=True
        )
        data = self._parse_json_object(self._to_text(raw))
        tool_ids = data.get("tool_ids") if isinstance(data.get("tool_ids"), list) else []
        agent_ids = data.get("agent_ids") if isinstance(data.get("agent_ids"), list) else []
        return [str(x) for x in tool_ids], [str(x) for x in agent_ids]

    @staticmethod
    def _need_tokens(prompt: str, capabilities: list[str]) -> set[str]:
        text = f"{prompt} {' '.join(capabilities or [])}"
        return {
            t
            for t in re.findall(r"[a-z0-9]+", text.lower())
            if len(t) >= 4 and t not in _STOPWORDS
        }

    @staticmethod
    def _match_catalog(catalog: list | None, need_tokens: set[str]) -> list[str]:
        if not catalog or not need_tokens:
            return []
        selected: list[str] = []
        for item in catalog:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "").strip()
            if not item_id:
                continue
            hay = f"{item.get('name', '')} {item.get('description', '')}".lower()
            item_tokens = {t for t in re.findall(r"[a-z0-9]+", hay) if len(t) >= 4}
            if need_tokens & item_tokens and item_id not in selected:
                selected.append(item_id)
        return selected

    # -- LLM path -------------------------------------------------------------

    async def _draft_with_llm(self, prompt: str) -> dict[str, Any]:
        raw = await self._llm.get_chat_completion(
            DRAFT_SYSTEM_PROMPT, prompt, json_mode=True
        )
        text = self._to_text(raw)
        return self._parse_json_object(text)

    @staticmethod
    def _to_text(raw: Any) -> str:
        if isinstance(raw, str):
            return raw
        if isinstance(raw, dict):
            return json.dumps(raw)
        # Best-effort for OpenAI-style response objects.
        choices = getattr(raw, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None)
            if content:
                return content
        return str(raw)

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        text = text.strip()
        # Tolerate ```json fences or leading prose.
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("LLM response did not contain a JSON object")
        data = json.loads(match.group(0))
        if not isinstance(data, dict):
            raise ValueError("LLM JSON was not an object")
        return data

    # -- Heuristic path -------------------------------------------------------

    def _heuristic_fields(self, prompt: str) -> dict[str, Any]:
        words = re.findall(r"[A-Za-z0-9']+", prompt)
        keywords = [w for w in words if w.lower() not in _STOPWORDS]
        headline = " ".join(keywords[:5]) or prompt[:40]
        name = f"{headline.title()} Agent".strip()

        capabilities: list[str] = []
        low = prompt.lower()
        for needle, slug in _CAPABILITY_HINTS.items():
            if needle in low and slug not in capabilities:
                capabilities.append(slug)
        if not capabilities:
            capabilities = ["general_assistant"]

        return {
            "name": name,
            "description": f"Business agent that handles: {prompt}",
            "business": (
                "You are a business agent for SMDG-AI. "
                f"Your responsibility: {prompt} "
                "Be accurate, cite your sources, and ask for missing details."
            ),
            "capabilities": capabilities,
            "tools": [],
            "workflows": [],
        }

    # -- Shared ---------------------------------------------------------------

    def _build_draft(self, prompt: str, fields: dict[str, Any]) -> BusinessAgentDraft:
        defaults = self._heuristic_fields(prompt)

        def _str(key: str) -> str:
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            return defaults[key]

        def _list(key: str) -> list[str]:
            value = fields.get(key)
            if isinstance(value, list):
                return [str(v).strip() for v in value if str(v).strip()]
            return list(defaults.get(key, []))

        # tools/workflows/agents are intentionally NOT taken from free-text model output —
        # they must be real registry IDs, assigned later via _assign_from_catalog().
        return BusinessAgentDraft(
            name=_str("name")[:255],
            description=_str("description"),
            business=_str("business"),
            capabilities=_list("capabilities"),
        )
