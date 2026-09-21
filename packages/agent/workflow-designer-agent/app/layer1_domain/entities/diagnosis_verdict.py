from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DiagnosisVerdict:
    """A failed-run diagnosis, as produced by the troubleshooting-agent's classifier.

    Mirrors the fields of that agent's DiagnosticReport.to_dict() so a diagnose response
    round-trips into the designer without coupling to the other service's classes.
    """

    run_id: str = ""
    category: str = ""
    severity: str = ""
    root_cause: str = ""
    recommended_fix: str = ""
    explanation: str = ""
    owner: str = ""
    confidence: float = 0.0
    failed_node_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    #: Category the classifier emits when the run did not actually fail.
    NO_FAILURE: str = "no_failure"

    def has_failure(self) -> bool:
        return bool(self.category) and self.category != self.NO_FAILURE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DiagnosisVerdict":
        data = data or {}
        return cls(
            run_id=str(data.get("run_id", "")),
            category=str(data.get("category", "")),
            severity=str(data.get("severity", "")),
            root_cause=str(data.get("root_cause", "")),
            recommended_fix=str(data.get("recommended_fix", "")),
            explanation=str(data.get("explanation", "")),
            owner=str(data.get("owner", "")),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            failed_node_ids=[str(x) for x in (data.get("failed_node_ids") or [])],
            evidence=[str(x) for x in (data.get("evidence") or [])],
        )
