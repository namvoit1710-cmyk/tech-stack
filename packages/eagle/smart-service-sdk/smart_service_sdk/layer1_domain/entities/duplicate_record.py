from dataclasses import dataclass, field


@dataclass(frozen=True)
class DuplicateRecord:
    record_id: str
    tenant_id: str
    source_type: str
    source_file_id: str = ""
    source_row_key: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    normalized_fields: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DuplicateRecordMatch:
    record: DuplicateRecord
    exact_matches: tuple[str, ...] = ()
    fuzzy_matches: dict[str, float] = field(default_factory=dict)
    vector_score: float | None = None
    graph_score: float | None = None
    matched_terms: tuple[str, ...] = ()
    graph_evidence: dict[str, object] = field(default_factory=dict)
