from dataclasses import dataclass, field


@dataclass
class DuplicateCandidate:
    record_id: str
    confidence: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class DuplicateCheckResult:
    request_id: str
    candidates: list[DuplicateCandidate] = field(default_factory=list)
