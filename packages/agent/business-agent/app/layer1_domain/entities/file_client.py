from dataclasses import dataclass, field
import json
from typing import Any, Dict, List


@dataclass
class FileData:
    file_id: str
    data: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FileData":
        return cls(
            file_id=d.get("file_id", ""),
            data=d.get("data", []),
        )

    def __str__(self) -> str:
        return json.dumps({"file_id": self.file_id, "data": self.data})
