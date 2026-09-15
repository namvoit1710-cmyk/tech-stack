from dataclasses import dataclass
from typing import Optional


@dataclass(kw_only=True)
class BaseDomainEntity:
    id: Optional[str] = None
