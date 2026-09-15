from dataclasses import dataclass


@dataclass
class TaskMetrics:
    duration_ms: float = 0.0
    input_bytes: int = 0
    output_bytes: int = 0
