
from dataclasses import dataclass


@dataclass
class DuplicateMatchRule:
    field: str
    match_type: str
    threshold: float | None = None
