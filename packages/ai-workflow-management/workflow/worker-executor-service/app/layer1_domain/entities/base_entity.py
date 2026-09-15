from dataclasses import dataclass
from typing import Optional


@dataclass(kw_only=True)
class BaseDomainEntity:
    """Pure Domain Entity base. Completely framework agnostic."""
    id: Optional[str] = None
