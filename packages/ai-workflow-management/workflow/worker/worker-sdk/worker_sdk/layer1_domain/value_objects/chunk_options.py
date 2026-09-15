from dataclasses import dataclass
from typing import Optional


@dataclass
class ChunkOptions:
    chunk_size: int = 1024 * 1024  # 1 MB default
    offset: int = 0
    limit: Optional[int] = None
