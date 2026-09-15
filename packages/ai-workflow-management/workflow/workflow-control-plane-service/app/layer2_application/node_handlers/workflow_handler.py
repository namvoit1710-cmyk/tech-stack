"""WORKFLOW node handler: invokes another workflow as a child sub-workflow."""

from __future__ import annotations

from typing import Any

from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.exceptions import MaxDepthExceededError, ExpressionError
from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import WorkflowNodeConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer1_domain.value_objects.event_id import EventId
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, VariableResolutionError


class WorkflowNodeHandler(AsyncInlineHandlerBase):
    def __init__(self, variable_resolver: VariableResolver) -> None:
        self._resolver = variable_resolver

    async def handle(
        self,
        node: WorkflowNode,
        run: WorkflowRun,
        workflow: Workflow,
        topology: GraphTopology,
        completed_task: Task | None,
        task_factory: TaskFactory,
    ) -> NodeHandlerResult:
        result = NodeHandlerResult()

        # Phase 6 — uniform execution fields.
        result.completion_mode = "child_run"

        result.requires_child_run = True
        # Canonical keys only (NodeSpec Phase 3): input_mapping is always
        # a dict — the array-of-rows form value folds at split time.
        cfg = WorkflowNodeConfig.from_node(node)
        max_depth = cfg.max_depth
        if not isinstance(max_depth, (int, float)) or isinstance(max_depth, bool):
            max_depth = 10
        else:
            max_depth = int(max_depth)
        if run.depth_level >= max_depth:
            raise MaxDepthExceededError(
                f"Max nesting depth {max_depth} exceeded at node '{node.id}'"
            )
        child_workflow_id = cfg.workflow_id
        if not child_workflow_id:
            raise ExpressionError(
                f"WORKFLOW node '{node.id}' missing config.workflow_id"
            )
        # SA-2365 / SA-2458: resolve the per-subflow environment binding. A
        # literal env id from the FE EnvironmentPickerControl passes through
        # unchanged; empty = inherit / No environment (handled by the
        # coordinator).
        #
        # SA-2496 "Dynamic environment (only id)": a value carrying an
        # expression (e.g. ``{{$input.environmentId}}``) is the DYNAMIC mode —
        # it is resolved against the parent run's variables and MUST yield a
        # concrete environment id. Unlike a literal empty value ("No
        # environment"), a dynamic value that resolves to empty/blank is an
        # ERROR (DYNAMIC_ENV_UNRESOLVED); it must never silently fall back to
        # "no env". The resolved id is then guarded against the target
        # subflow's SA-2450 assignment by the ChildWorkflowCoordinator.
        environment_id = cfg.environment_id or ""
        environment_dynamic = bool(environment_id) and (
            "{{" in environment_id or environment_id.startswith("$")
        )
        if environment_dynamic:
            try:
                _resolved_env = await self._resolver.resolve_mapping(
                    {"environment_id": environment_id}, run.context.variables
                )
                environment_id = str(_resolved_env.get("environment_id") or "").strip()
            except VariableResolutionError as exc:
                raise ExpressionError(
                    f"WORKFLOW node '{node.id}': DYNAMIC_ENV_UNRESOLVED — "
                    f"environment expression could not be resolved: {exc}"
                ) from exc
            if not environment_id:
                raise ExpressionError(
                    f"WORKFLOW node '{node.id}': DYNAMIC_ENV_UNRESOLVED — the "
                    f"dynamic environment expression resolved to an empty value; "
                    f"it must resolve to an environment id."
                )

        input_mapping = cfg.input_mapping
        child_input: dict[str, Any] = {}
        if input_mapping:
            try:
                child_input = await self._resolver.resolve_mapping(
                    input_mapping, run.context.variables
                )
            except VariableResolutionError as exc:
                raise ExpressionError(
                    f"WORKFLOW node '{node.id}': input mapping resolution failed: {exc}"
                ) from exc
        # Deterministic child-run id: same (run, node, generation) → same id.
        # A redelivered or replayed dispatch of this WORKFLOW node reuses the
        # existing child run id instead of spawning an orphaned duplicate (the
        # old wall-clock suffix minted a fresh child per invocation). The
        # ``generation`` component bumps on reset/re-execute (run-generation
        # guard), so a genuine new execution still gets a fresh child run.
        #
        # uuid5-hashed (not the legacy ``{run}_{node}_{gen}`` concatenation)
        # so the id stays a fixed 36 chars at ANY nesting depth — the
        # concatenated form grew ~39 chars/level and overflowed downstream
        # RUN_ID columns past ~5 nested sub-workflows.
        #
        # ``visit`` (SA-846 revisit counter) keys cycle re-entries: each
        # revisit of this WORKFLOW node spawns a fresh child run while a
        # redelivered dispatch WITHIN a visit still dedupes to the same id.
        predefined_run_id = EventId.derive_child_run(
            run.id, node.id, run.generation,
            visit=run.context.node_visits.get(node.id, 0),
        )
        parent_input = run.input_data or {}

        tracking_task = task_factory.create_task(node, run)
        result.state_updates[node.id] = {
            "output": {},
            "metadata": {
                "type": "workflow",
                "child_workflow_id": child_workflow_id,
                # SA-2365: bind the child sub-workflow run to a specific
                # environment ("" = inherit the parent run's $env). The
                # coordinator turns this into the child's $env source.
                "environment_id": environment_id,
                # SA-2496: only the DYNAMIC (expression-resolved) path is guarded
                # against the subflow's SA-2450 assignment; a literal id chosen in
                # the picker keeps its existing SA-2458 behavior.
                "environment_dynamic": environment_dynamic,
                "child_input": child_input,
                "predefined_run_id": predefined_run_id,
                "parent_input": parent_input,
                "tracking_task_id": tracking_task.id,
                "on_failure": cfg.on_failure,
                "output_binding": cfg.output_binding,
            },
        }
        result.tasks_to_dispatch.append(tracking_task)
        return result
