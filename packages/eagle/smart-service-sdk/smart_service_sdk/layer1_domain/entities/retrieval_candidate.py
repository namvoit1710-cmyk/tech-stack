from dataclasses import dataclass, field

from smart_service_sdk.layer1_domain.entities.retrieval_query import RetrievalRoute


@dataclass(frozen=True)
class RetrievalCandidate:
    document_id: str
    chunk_id: str
    content: str
    score: float
    route: RetrievalRoute
    metadata: dict[str, object] = field(default_factory=dict)
