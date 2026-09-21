import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import List, Optional


class FileStatus(str, Enum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PARSED = "parsed"
    ERROR = "error"


# -----------------------------
# ENTITIES
# -----------------------------
@dataclass
class KnowledgeItem:
    document_id: str
    summary: str


@dataclass
class FileObject:
    file_id: str
    file_path: Optional[str] = field(
        default=None,
        metadata={"description": "Resolved path in S3/MinIO"},
    )
    status: FileStatus = field(
        default=FileStatus.UPLOADED,
        metadata={"description": "uploaded, processing, parsed, error"},
    )


@dataclass
class TaskHistory:
    task_id: str
    knowledge: List[KnowledgeItem] = field(default_factory=list)


@dataclass
class MessageHistory:
    message_id: str
    message: str
    tasks: Optional[List[TaskHistory]] = field(default_factory=list)
    files: Optional[List[FileObject]] = field(
        default_factory=list,
        metadata={"description": "Files attached to this context"},
    )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, value: str) -> "MessageHistory":
        data = json.loads(value)

        tasks = []
        for task_data in data.get("tasks", []):
            knowledge = [KnowledgeItem(**k) for k in task_data.get("knowledge", [])]

            tasks.append(
                TaskHistory(
                    task_id=task_data["task_id"],
                    knowledge=knowledge,
                )
            )

        return cls(
            message_id=data["message_id"],
            message=data["message"],
            tasks=tasks,
        )
