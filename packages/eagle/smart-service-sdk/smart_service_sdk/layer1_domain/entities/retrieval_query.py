from dataclasses import dataclass, field
from enum import Enum


class RetrievalRoute(str, Enum):
    STRUCTURED_TABLE = "structured_table"
    STRUCTURED_SPREADSHEET = "structured_spreadsheet"
    HYBRID_DOCUMENT = "hybrid_document"


@dataclass(frozen=True)
class RetrievalQuery:
    text: str
    filters: dict[str, object] = field(default_factory=dict)
    top_k: int = 10
