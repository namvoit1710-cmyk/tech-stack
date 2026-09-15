"""Dry-run a workflow: simulate execution without side effects."""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.read.schema_validator import SchemaValidator
from app.layer2_application.resolve.variable_resolver import (
    VariableResolver,
    TraversalResolverStrategy,
    VariableResolutionError,
)
from app.layer1_domain.value_objects.node_type import NodeType


@dataclass
class DryRunWorkflowCommand:
    workflow_id: str = ""
    input_data: dict[str, Any] | None = None


@dataclass
class DryRunStep:
    order: int = 0
    node_id: str = ""
    node_name: str = ""
    node_type: str = ""
    description: str = ""
    worker_type: str | None = None
    input_binding: dict[str, Any] = field(default_factory=dict)
    resolved_input: dict[str, Any] | None = None
    config_summary: dict[str, Any] = field(default_factory=dict)
    outgoing_edges: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DryRunWorkflowResult:
    workflow_id: str = ""
    workflow_name: str = ""
    is_valid: bool = False
    validation_errors: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class DryRunWorkflowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: DryRunWorkflowCommand) -> DryRunWorkflowResult:
        self.logger.info("Dry-running workflow", id=command.workflow_id)

        # 1. Lookup workflow
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return DryRunWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        result = DryRunWorkflowResult(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
        )

        # 2. Schema validation (structural DAG validation removed)
        effective_input = command.input_data if command.input_data is not None else {}
        if workflow.input_schema:
            if command.input_data is not None:
                schema_errors = SchemaValidator.validate_input(
                    effective_input, workflow.input_schema
                )
                if schema_errors:
                    result.schema_errors = schema_errors
                    return result
            else:
                result.warnings.append(
                    "Workflow defines input_schema but no input_data was provided; "
                    "schema validation skipped"
                )

        # 4. Build DAG
        topology = GraphTopology.from_workflow(workflow)

        # 5. Topological sort (Kahn's algorithm)
        in_degree: dict[str, int] = {n.id: 0 for n in workflow.nodes}
        for successors in topology.adjacency.values():
            for s in successors:
                in_degree[s] += 1

        queue: list[str] = sorted(
            [nid for nid, deg in in_degree.items() if deg == 0]
        )
        topo_order: list[str] = []
        while queue:
            queue.sort()
            node_id = queue.pop(0)
            topo_order.append(node_id)
            for successor in topology.adjacency.get(node_id, []):
                in_degree[successor] -= 1
                if in_degree[successor] == 0:
                    queue.append(successor)

        # 6. Build node lookup
        node_map = {n.id: n for n in workflow.nodes}

        # 7. Variable resolver for best-effort resolution
        resolver = VariableResolver([TraversalResolverStrategy()])
        sim_context: dict[str, Any] = {"input": effective_input}

        # 8. Walk nodes in topo order
        step_list: list[DryRunStep] = []
        node_type_counts: dict[str, int] = {}
        has_loops = False
        has_conditions = False
        has_parallel = False
        has_sub_workflows = False

        for order, nid in enumerate(topo_order):
            node = node_map[nid]
            ntype = node.node_type.value if isinstance(node.node_type, NodeType) else str(node.node_type)

            node_type_counts[ntype] = node_type_counts.get(ntype, 0) + 1

            step = DryRunStep(
                order=order,
                node_id=nid,
                node_name=node.name,
                node_type=ntype,
                input_binding=node.input_binding,
            )

            # Outgoing edges
            outgoing_edges = workflow.get_outgoing_edges(nid)
            step.outgoing_edges = [
                {
                    "target_node_id": e.target_node_id,
                    "condition": e.condition,
                }
                for e in outgoing_edges
            ]

            # Type-specific simulation
            config = node.config or {}

            if node.node_type == NodeType.TRIGGER:
                trigger_type = config.get("trigger_type", "manual")
                step.description = f"Initializes workflow context with trigger type '{trigger_type}'"
                step.config_summary = {"trigger_type": trigger_type}

            elif node.node_type == NodeType.TASK:
                worker_id = node.worker_type
                step.worker_type = worker_id
                if worker_id:
                    step.description = f"Dispatches task to worker '{worker_id}'"
                else:
                    step.description = "Dispatches task to worker (unspecified)"
                    step.warnings.append("Missing worker_type")

                # Best-effort variable resolution
                if node.input_binding:
                    try:
                        resolved = await resolver.resolve_mapping(
                            node.input_binding, sim_context
                        )
                        step.resolved_input = resolved
                    except VariableResolutionError:
                        step.resolved_input = None
                        step.warnings.append(
                            "Some expressions reference upstream runtime output "
                            "and cannot be resolved in dry-run"
                        )

            elif node.node_type == NodeType.CONDITION:
                has_conditions = True
                expression = config.get("expression", "")
                step.description = f"Evaluates condition: '{expression}'"
                step.config_summary = {"expression": expression}
                step.warnings.append("Branch taken depends on runtime evaluation")

            elif node.node_type == NodeType.SWITCH:
                has_conditions = True
                expression = config.get("expression", "")
                cases = config.get("cases", [])
                step.description = f"Multi-way switch on: '{expression}'"
                step.config_summary = {"expression": expression, "cases": cases}
                step.warnings.append("Branch taken depends on runtime evaluation")

            elif node.node_type == NodeType.LOOP:
                has_loops = True
                collection = config.get("collection", "")
                mode = config.get("mode", "sequential")
                max_iterations = config.get("max_iterations", None)
                step.description = f"Iterates over '{collection}'"
                step.config_summary = {
                    "collection": collection,
                    "mode": mode,
                    "max_iterations": max_iterations,
                }

            elif node.node_type == NodeType.PARALLEL:
                has_parallel = True
                branch_count = len(outgoing_edges)
                step.description = f"Fan-out to {branch_count} parallel branches"
                step.config_summary = {"branch_count": branch_count}

            elif node.node_type == NodeType.MERGE:
                predecessor_ids = topology.predecessors.get(nid, [])
                merge_strategy = config.get("merge_strategy", "all")
                step.description = f"Fan-in: waits for predecessors (strategy: '{merge_strategy}')"
                step.config_summary = {
                    "merge_strategy": merge_strategy,
                    "predecessor_ids": predecessor_ids,
                }

            elif node.node_type == NodeType.WORKFLOW:
                has_sub_workflows = True
                child_workflow_id = config.get("workflow_id", "")
                max_depth = config.get("max_depth", None)
                step.description = f"Invokes sub-workflow '{child_workflow_id}'"
                step.config_summary = {
                    "child_workflow_id": child_workflow_id,
                    "max_depth": max_depth,
                }

            elif node.node_type == NodeType.COMPUTE:
                expression = config.get("expression", "")
                result_key = config.get("result_key", "")
                step.description = f"Evaluates expression: '{expression}'"
                step.config_summary = {
                    "expression": expression,
                    "result_key": result_key,
                }

            else:
                step.description = f"Node of type '{ntype}'"

            step_list.append(step)

        # 9. Build summary
        task_nodes_count = node_type_counts.get("TASK", 0)
        result.summary = {
            "total_nodes": len(workflow.nodes),
            "total_edges": len(workflow.edges),
            "entry_nodes": topology.entry_nodes,
            "leaf_nodes": topology.leaf_nodes,
            "node_type_counts": node_type_counts,
            "task_nodes_count": task_nodes_count,
            "has_loops": has_loops,
            "has_conditions": has_conditions,
            "has_parallel": has_parallel,
            "has_sub_workflows": has_sub_workflows,
        }

        # 10. Return result
        result.is_valid = True
        result.steps = [dataclasses.asdict(s) for s in step_list]
        return result
