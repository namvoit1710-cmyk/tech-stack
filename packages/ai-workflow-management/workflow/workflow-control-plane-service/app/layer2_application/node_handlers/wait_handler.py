"""WAIT node handler: suspends the run until a durable timer fires (SA-1371 S4).

Mirrors ``InputNodeHandler`` — it is a ``PendingHandlerBase`` subclass, so the
base ``handle`` template method parks the run *exactly* the same way an INPUT
node parks awaiting user input: it creates a ``PENDING`` task, appends it to
``tasks_to_dispatch``, sets ``completion_mode = "pending"`` and emits an
``InputRequested`` event (here with ``pending_kind = "wait"``).

The one WAIT-specific step is scheduling the durable timer. ``handle`` computes
``fire_at`` from the config's ``mode`` and invokes ``CreateTimerUseCase`` (keyed
deterministically to ``(run_id, node_id, kind)``) *before* delegating to
``super().handle()`` for the park. When the leader-gated ``TimerDispatcher``
later fires the timer, the ``TimerFiredConsumer`` resumes the run through the
same ``resume_run`` / ``resume_pending_task`` path a user-submit of an INPUT
node takes — the parked task is COMPLETED (with empty output) and successors
are dispatched.

There is no user-facing form: ``build_input_schema`` returns ``[]`` and
``provision_artifacts`` returns ``None``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, ClassVar

from app.layer1_domain.events import (
    DomainEvent,
    EventTopic,
    InputRequested,
    InputSubmitted,
)
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import WaitConfig
from app.layer2_application.features.create_timer.use_cases.create_timer_use_case import (
    CreateTimerCommand,
)
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.pending_base import PendingHandlerBase
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver

logger = logging.getLogger(__name__)

# WAIT ``mode`` → durable-timer ``kind``. Timer kinds are the canonical
# ``"delay" | "sla" | "schedule"`` vocabulary declared on the Timer entity;
# the "until" mode fires at a fixed scheduled instant, so it maps to "schedule".
_MODE_TO_KIND: dict[str, str] = {
    "delay": "delay",
    "until": "schedule",
    "sla": "sla",
}


class WaitNodeHandler(PendingHandlerBase):
    """Parks the run and schedules a durable timer that later resumes it."""

    pending_kind: ClassVar[str] = "wait"

    def __init__(
        self,
        variable_resolver: VariableResolver,
        create_timer_use_case: Any = None,
    ) -> None:
        super().__init__(variable_resolver)
        self._create_timer = create_timer_use_case

    # ------------------------------------------------------------------
    # fire_at computation
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @classmethod
    def compute_fire_at(cls, cfg: WaitConfig, *, now: datetime | None = None) -> str:
        """Return the ISO-8601 instant the timer should fire at.

        ``delay`` → now + ``delay_seconds``; ``until`` → the ``until``
        timestamp verbatim; ``sla`` → now + ``sla_timeout_seconds``.
        """
        base = now or cls._now()
        mode = (cfg.mode or "delay").lower()
        if mode == "until":
            return cfg.until or ""
        if mode == "sla":
            seconds = int(cfg.sla_timeout_seconds or 0)
        else:  # delay (default)
            seconds = int(cfg.delay_seconds or 0)
        return (base + timedelta(seconds=seconds)).isoformat()

    # ------------------------------------------------------------------
    # Template method — schedule the timer, then park via the base
    # ------------------------------------------------------------------

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        cfg = WaitConfig.from_node(node)
        kind = _MODE_TO_KIND.get((cfg.mode or "delay").lower(), "delay")
        fire_at = self.compute_fire_at(cfg)

        if self._create_timer is not None:
            await self._create_timer.execute(CreateTimerCommand(
                run_id=run.id,
                workflow_id=run.workflow_id,
                node_id=node.id,
                kind=kind,
                fire_at=fire_at,
            ))
        else:  # pragma: no cover — defensive: handler must be wired with a UC
            logger.warning(
                "WaitNodeHandler has no CreateTimerUseCase wired; node %s on "
                "run %s parked but no timer scheduled", node.id, run.id,
            )

        # Park the run exactly like an INPUT node (PendingHandlerBase.handle
        # creates the PENDING task, sets completion_mode="pending", and emits
        # the InputRequested event our build_* hooks below shape).
        return await super().handle(
            node, run, workflow, topology, completed_task, task_factory,
        )

    # ------------------------------------------------------------------
    # PendingHandlerBase hooks — no user-facing form
    # ------------------------------------------------------------------

    async def build_input_schema(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        prefilled: dict[str, Any],
        ctx: dict[str, Any],
    ) -> list:
        return []

    def build_output_metadata(
        self,
        *,
        node: WorkflowNode,
        run: WorkflowRun,
        resolved_input: Any,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = WaitConfig.from_node(node)
        return {
            "node_type": "WAIT",
            "input_schema": input_schema,
            "instruction": ctx.get("instruction", ""),
            "prefilled": prefilled,
            "wait_mode": cfg.mode,
            "fire_at": self.compute_fire_at(cfg),
        }

    def build_input_requested_event(
        self,
        *,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        instruction: str,
        prefilled: dict[str, Any],
        input_schema: list,
        ctx: dict[str, Any],
    ) -> DomainEvent:
        return InputRequested(
            task_id=task.id,
            run_id=run.id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            instruction=instruction,
            pending_kind="wait",
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
            parent_run_id=run.parent_run_id or "",
        )

    def build_completion_event(
        self,
        task: Task,
        run: WorkflowRun,
        node: WorkflowNode,
        action: str,
    ) -> tuple[str, DomainEvent]:
        payload = InputSubmitted(
            task_id=task.id,
            run_id=task.run_id,
            workflow_id=run.workflow_id,
            node_id=node.id,
            pending_kind="wait",
            root_run_id=run.root_run_id or run.id,
            depth=run.depth_level,
            source="workflow-control-plane",
        )
        return EventTopic.INPUT_SUBMITTED, payload
