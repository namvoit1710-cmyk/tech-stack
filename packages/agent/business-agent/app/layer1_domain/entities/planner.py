from dataclasses import dataclass, field
from typing import Any, List, Literal, Optional, Dict
from enum import Enum

from app.layer1_domain.entities.next_step_suggestion import NextStepSuggestion


# -------------------------
# Enum
# -------------------------

class PlannerResponseType(str, Enum):
    DIRECT_ANSWER = "direct_answer"
    EXECUTION_PLAN = "execution_plan"
    SUMMARY = "summary"


class PatchInputMode(str, Enum):
    FILL = "fill"
    PARTIAL_NO_RAG = "partial_no_rag"
    RAG_REQUEST = "rag_request"
# -------------------------
# PlanStep
# -------------------------

@dataclass
class PlanStep:
    order: int | None
    tool: str
    input_data: dict[str, Any]
    output_data: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        data = dict(data)

        # Alias support (input_data OR input_schema)
        input_data = data.get("input_data", {})
        if input_data is None:
            raise ValueError("PlanStep requires 'input_data'")

        # Alias support (output_data OR output_schema)
        output_data = data.get("output_data", {})

        return cls(
            order=data.get("order"),
            tool=data["tool"],
            input_data=input_data,
            output_data=output_data,
            reason=data.get("reason"),
        )


# -------------------------
# PlannerResponse
# -------------------------

@dataclass
class PlannerResponse:
    type: PlannerResponseType

    goal: str | None = None
    answer: str | None = None
    steps: list[PlanStep] = field(default_factory=list)
    suggestions: list[NextStepSuggestion] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlannerResponse":
        data = dict(data)

        steps_raw = data.get("steps", []) or []

        return cls(
            type=PlannerResponseType(data["type"]),
            goal=data.get("goal"),
            answer=data.get("answer"),
            steps=[PlanStep.from_dict(s) for s in steps_raw],
            suggestions=cls._parse_suggestions(data.get("next_steps")),
        )

    @staticmethod
    def _parse_suggestions(raw: Any) -> list[NextStepSuggestion]:
        # Suggestions are best-effort: a malformed entry is skipped rather than
        # failing the whole response, so next-step hints never break the summary.
        # Cap at 4 to bound the event payload regardless of model output.
        if not isinstance(raw, list):
            return []
        suggestions: list[NextStepSuggestion] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            try:
                suggestions.append(NextStepSuggestion.from_dict(item))
            except (ValueError, TypeError):
                continue
        return suggestions[:4]
    
@dataclass
class PatchInputNodeResult:
    mode: PatchInputMode

    filled_fields: Dict[str, str] = field(default_factory=dict)

    missing_fields: List[str] = field(default_factory=list)

    reason: Optional[str] = None

    required_context: Optional[str] = None

    document_ids: List[str] = field(default_factory=list)

    knowledge_base_ids: List[str] = field(default_factory=list)

    suggesion_rag_query: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "PatchInputNodeResult":
        try:
            mode = PatchInputMode(data.get("mode"))
        except Exception:
            raise ValueError(f"Invalid mode: {data.get('mode')}")

        return cls(
            mode=mode,
            filled_fields=data.get("filled_fields", {}) or {},
            missing_fields=data.get("missing_fields", []) or [],
            reason=data.get("reason"),
            required_context=data.get("required_context"),
            document_ids=data.get("document_ids", []) or [],
            knowledge_base_ids=data.get("knowledge_base_ids", []) or [],
            suggesion_rag_query=data.get("suggesion_rag_query"),
        )