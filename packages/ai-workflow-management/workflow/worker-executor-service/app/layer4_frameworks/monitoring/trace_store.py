"""In-memory task execution trace store.

Keeps the last *max_traces* execution traces in a ring buffer.
Each trace records timing spans for the stages of a single task execution.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TraceSpan:
    """A single timed span within a task trace."""
    name: str
    started_at: float          # time.monotonic() value
    ended_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        if self.ended_at <= 0:
            return 0.0
        return (self.ended_at - self.started_at) * 1000

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "duration_ms": round(self.duration_ms, 2),
            "metadata": self.metadata,
        }


@dataclass
class TaskTrace:
    """Complete execution trace for one task."""
    task_id: str
    run_id: str = ""
    node_id: str = ""
    worker_type: str = ""
    created_at: float = field(default_factory=time.time)  # wall-clock for display
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float = 0.0
    status: str = ""
    error: str = ""
    spans: list[TraceSpan] = field(default_factory=list)

    @property
    def total_duration_ms(self) -> float:
        if self.ended_at <= 0:
            return 0.0
        return (self.ended_at - self.started_at) * 1000

    def add_span(self, name: str, **metadata: Any) -> TraceSpan:
        span = TraceSpan(name=name, started_at=time.monotonic(), metadata=metadata)
        self.spans.append(span)
        return span

    def end_span(self, span: TraceSpan, **metadata: Any) -> None:
        span.ended_at = time.monotonic()
        if metadata:
            span.metadata.update(metadata)

    def finish(self, status: str, error: str = "") -> None:
        self.ended_at = time.monotonic()
        self.status = status
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        from datetime import datetime, timezone
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "node_id": self.node_id,
            "worker_type": self.worker_type,
            "status": self.status,
            "error": self.error,
            "created_at": datetime.fromtimestamp(self.created_at, tz=timezone.utc).isoformat(),
            "total_duration_ms": round(self.total_duration_ms, 2),
            "spans": [s.to_dict() for s in self.spans],
        }


class TaskTraceStore:
    """Thread-safe ring buffer of recent task traces."""

    def __init__(self, max_traces: int = 200) -> None:
        self._max = max_traces
        self._traces: deque[TaskTrace] = deque(maxlen=max_traces)
        self._lock = threading.Lock()
        self._active: dict[str, TaskTrace] = {}

    def start_trace(
        self,
        task_id: str,
        run_id: str = "",
        node_id: str = "",
        worker_type: str = "",
    ) -> TaskTrace:
        trace = TaskTrace(
            task_id=task_id,
            run_id=run_id,
            node_id=node_id,
            worker_type=worker_type,
        )
        with self._lock:
            self._active[task_id] = trace
        return trace

    def finish_trace(self, trace: TaskTrace, status: str, error: str = "") -> None:
        trace.finish(status, error)
        with self._lock:
            self._active.pop(trace.task_id, None)
            self._traces.append(trace)

    def get_active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in self._active.values()]

    def get_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            traces = list(self._traces)
        # Most recent first
        return [t.to_dict() for t in reversed(traces[-limit:])]

    def get_active_trace(self, task_id: str) -> TaskTrace | None:
        """Return the active (in-progress) trace for the given task, or None."""
        with self._lock:
            return self._active.get(task_id)

    def get_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            if task_id in self._active:
                return self._active[task_id].to_dict()
            for t in reversed(self._traces):
                if t.task_id == task_id:
                    return t.to_dict()
        return None

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            completed = [t for t in self._traces if t.status == "COMPLETED"]
            failed = [t for t in self._traces if t.status == "FAILED"]
            durations = [t.total_duration_ms for t in completed if t.total_duration_ms > 0]
        return {
            "active_traces": len(self._active),
            "stored_traces": len(self._traces),
            "max_traces": self._max,
            "completed_count": len(completed),
            "failed_count": len(failed),
            "avg_duration_ms": round(sum(durations) / len(durations), 2) if durations else 0,
            "p95_duration_ms": round(sorted(durations)[int(len(durations) * 0.95)] if len(durations) >= 2 else (durations[0] if durations else 0), 2),
            "max_duration_ms": round(max(durations), 2) if durations else 0,
        }
