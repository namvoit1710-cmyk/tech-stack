"""Task routing entity."""
from dataclasses import dataclass


@dataclass
class TaskRoute:
    """Represents a task-to-worker routing decision."""
    task_id: str = ""
    capability: str = ""
    selected_worker_id: str = ""
