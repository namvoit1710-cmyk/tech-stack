from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class DataMetadata:
    content_type: str = "application/octet-stream"
    size_bytes: Optional[int] = None
    checksum: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)
