import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid

from app.layer1_domain.value_objects.loop_scope import LoopScope
from app.layer1_domain.value_objects.task_status import TaskStatus
from app.layer1_domain.entities.base_entity import BaseDomainEntity


@dataclass(kw_only=True)
class Task(BaseDomainEntity):
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    node_id: str = ""
    status: TaskStatus = TaskStatus.READY
    worker_type: str | None = None
    resolved_input: dict[str, Any] | None = None
    output_ref: str | None = None
    output_metadata: dict[str, Any] | None = None
    scope_stack: list[LoopScope] | None = None
    # TODO: Retry logic is not yet implemented in the orchestration engine.
    # When a task fails and retry_count < max_retries, the engine should
    # re-dispatch the task instead of immediately failing the run.
    # See orchestration_engine.py orchestrate_fail().
    retry_count: int = 0
    max_retries: int = 3
    error: str | None = None
    idempotency_key: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    # The worker's own reported runtime. Declared here because
    # ``complete_task_use_case`` has always ASSIGNED it — on a plain dataclass
    # that silently created the attribute, so it existed only on a task that had
    # just come back from a worker with a non-zero duration, and only until the
    # next reload. ``orchestration_engine._mark_task_completed`` then read it
    # unconditionally and raised ``AttributeError`` on every task that had not
    # been through that exact path.
    #
    # NOT persisted: ``hana_task_repo`` writes an explicit column list and this
    # is not in it, so a reloaded task reports 0. That is a separate gap and is
    # deliberately not closed here — it needs a column and a migration, and the
    # one reader today is the LOOP tracking task, which the engine drives
    # terminal itself and for which 0 is the correct answer.
    execution_time_ms: int = 0
    # G-guard: generation of the WorkflowRun at the time this task was
    # created. Callback handlers reject tasks where
    # ``run_generation < run.generation`` — i.e. the task belongs to a
    # previous execution that has since been superseded by a reset.
    run_generation: int = 0

    @classmethod
    def create(cls, run_id: str, node_id: str, **kwargs: Any) -> "Task":
        if not run_id or not node_id:
            raise ValueError("run_id and node_id must not be empty")
        return cls(run_id=run_id, node_id=node_id, **kwargs)

    # ─────────────── Phase 4c.4 loop-scope helpers ──────────────────

    def get_innermost_scope(self) -> LoopScope | None:
        """Return the innermost enclosing loop scope for this task.

        Reads from ``scope_stack`` when the Phase 4c.4 field is
        populated.

        Returns ``None`` when the task is NOT inside any loop.
        """
        if self.scope_stack:
            return self.scope_stack[-1]
        return None

    def with_pushed_scope(self, scope: LoopScope) -> "Task":
        """Return a copy of this task with ``scope`` appended to the
        ``scope_stack``.

        Immutable-style builder — does NOT mutate the source task.
        Used by the future event-driven loop dispatcher when
        constructing body tasks for a new iteration: it takes the
        template task, pushes the iteration's scope onto the stack,
        and dispatches the copy.

        If the source has no existing ``scope_stack``, creates a new
        single-element list. Otherwise appends to a copy of the
        existing list (preserves immutability).
        """
        new_stack = list(self.scope_stack) if self.scope_stack else []
        new_stack.append(scope)
        return dataclasses.replace(self, scope_stack=new_stack)


@dataclass(kw_only=True)
class TaskResult:
    task_id: str = ""
    run_id: str = ""
    node_id: str = ""
    status: str = "COMPLETED"
    output_reference: str = ""
    output_metadata: dict[str, Any] = field(default_factory=dict)
    execution_time_ms: int = 0
    error: dict[str, Any] | None = None
    idempotency_key: str = ""
