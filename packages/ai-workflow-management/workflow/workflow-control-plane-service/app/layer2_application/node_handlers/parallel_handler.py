"""PARALLEL node handler: fan-out, dispatches multiple branches simultaneously."""

from __future__ import annotations

import logging

from app.layer1_domain.entities.task import Task
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.entities.workflow_run import GraphTopology, WorkflowRun
from app.layer1_domain.node_spec.specs import ParallelConfig
from app.layer2_application.node_handlers.base import NodeHandlerResult
from app.layer2_application.node_handlers.inline_bases import (
    AsyncInlineHandlerBase,
)
from app.layer2_application.orchestration.task_factory import TaskFactory
from app.layer2_application.resolve.variable_resolver import VariableResolver, VariableResolutionError


class ParallelNodeHandler(AsyncInlineHandlerBase):
    def __init__(self, variable_resolver: VariableResolver | None = None) -> None:
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
        result.output_port = "out_default"
        result.completion_mode = "immediate"

        result.state_updates[node.id] = {
            "output": {},
            "metadata": {"type": "parallel"},
        }
        # Edge-based dispatch: branch conditions still gate which outgoing
        # edges are followed via ``taken_edge_ports``; downstream skip is
        # no longer pre-computed.
        branches = ParallelConfig.from_node(node).branches
        if branches and self._resolver:
            outgoing = topology.edge_map.get(node.id, [])
            for branch in branches:
                branch_condition = branch.get("condition")
                branch_nodes = set(branch.get("nodes", []))
                if not branch_nodes:
                    continue
                condition_met = True
                if branch_condition:
                    try:
                        condition_met = bool(
                            await self._resolver.resolve(branch_condition, run.context.variables)
                        )
                    except VariableResolutionError as exc:
                        logging.getLogger(__name__).warning(
                            "VariableResolutionError evaluating branch condition for parallel node %s: %s — defaulting to False",
                            node.id, exc,
                        )
                        condition_met = False
                if condition_met:
                    for edge in outgoing:
                        if edge.target_node_id in branch_nodes:
                            result.taken_edge_ports[edge.id] = (
                                edge.source_port_id or "out_default"
                            )
                else:
                    # Gated-OFF branch: its nodes are neither dispatched nor
                    # reachable on this run. Mark them skipped so a downstream
                    # MERGE counts them toward quorum (``merge_handler`` adds
                    # any predecessor in ``skipped_nodes`` to its buffer). Without
                    # this a MERGE with ``wait_all`` waits on a branch that will
                    # never arrive → the run hangs in RUNNING forever. This
                    # restores the pre-edge-dispatch behaviour (the engine
                    # consumes ``skipped_node_ids`` in ``_run_immediate``).
                    result.skipped_node_ids.extend(sorted(branch_nodes))
        return result
