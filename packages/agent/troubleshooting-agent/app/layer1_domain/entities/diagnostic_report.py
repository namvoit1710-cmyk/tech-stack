from dataclasses import dataclass, field
from typing import Any

from app.layer1_domain.entities.base_entity import BaseEntity
from app.layer1_domain.value_objects.failure_category import OWNER, FailureCategory


@dataclass
class DiagnosticReport(BaseEntity):
    """The agent's deliverable: a structured diagnosis of one workflow run."""

    run_id: str = ""
    category: FailureCategory = FailureCategory.UNKNOWN
    severity: str = "unknown"          # none | low | medium | high
    root_cause: str = ""               # technical cause (for engineers / agent_data)
    recommended_fix: str = ""
    explanation: str = ""              # plain-language narrative for the end user (presentation)
    confidence: float = 0.0
    failed_node_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    @property
    def owner(self) -> str:
        """Who should act — derived purely from category, so it's identical for the same
        verdict regardless of whether the deterministic or LLM path produced it."""
        return OWNER.get(self.category, "dev")

    def to_dict(self) -> dict[str, Any]:
        """Plain JSON-serializable form (enum → str) for LangGraph state + API responses."""
        return {
            "run_id": self.run_id,
            "category": self.category.value,
            "owner": self.owner,
            "severity": self.severity,
            "root_cause": self.root_cause,
            "recommended_fix": self.recommended_fix,
            "explanation": self.explanation,
            "confidence": self.confidence,
            "failed_node_ids": list(self.failed_node_ids),
            "evidence": list(self.evidence),
        }
