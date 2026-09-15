"""LOOP node handler: delegates to EventDrivenLoopDriver.

Phase 6.3 simplified this handler — the ``event_driven`` kwarg and
all legacy code paths (``_initialize_loop``, ``_handle_iteration_complete``,
``_dispatch_exit``, ``_get_body_entry``) have been removed. The handler
delegates to ``EventDrivenLoopDriver`` for both initialization and
iteration-complete, then sets ``completion_mode`` for the engine.
"""

from __future__ import annotations

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer2_application.orchestration.loop_driver import EventDrivenLoopDriver
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver
from typing import Any


class LoopNodeHandler(AsyncInlineHandlerBase):
    def __init__(
        self,
        variable_resolver: VariableResolver,
        data_store_service: Any = None,
        **_kwargs: Any,
    ) -> None:
        self._resolver = variable_resolver
        # Pass the store so the driver can materialize file-backed loop
        # collections ({{$node.items}} → array_field ref) instead of
        # silently iterating zero times (SA-1905 D2).
        self._driver = EventDrivenLoopDriver(
            variable_resolver, data_store_service=data_store_service,
        )

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        loop_state = run.context.loop_state.get(node.id)
        if loop_state and completed_task:
            result = await self._driver.iteration_complete(
                node, run, workflow, topology, completed_task,
                task_factory, loop_state,
            )
        else:
            result = await self._driver.initialize_loop(
                node, run, workflow, topology, completed_task, task_factory,
            )

        # Set completion_mode based on whether the driver produced
        # body tasks (loop_body) or exit/no tasks (immediate).
        ls = result.loop_state_updates or {}
        loop_total = ls.get(node.id, {}).get("total", 0) if isinstance(ls.get(node.id), dict) else 0
        if not loop_total and loop_state:
            loop_total = loop_state.get("total", 0)
        completed_iters = (
            loop_state.get("completed_iterations", []) if loop_state else []
        )
        if loop_total > 0 and len(completed_iters) < loop_total:
            result.completion_mode = "loop_body"
        else:
            result.completion_mode = "immediate"
            result.output_port = "loop_complete"

        return result
