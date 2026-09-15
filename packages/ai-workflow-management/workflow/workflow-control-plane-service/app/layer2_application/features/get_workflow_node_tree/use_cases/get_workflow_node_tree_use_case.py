"""Get workflow node data as a tree with nested sub-workflow resolution."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode
from app.layer1_domain.node_spec.parameter_mapper import merge_parameters
from app.layer1_domain.value_objects.node_type import NodeType


@dataclass
class GetWorkflowNodeTreeCommand:
    workflow_id: str = ""
    max_depth: int = 10
    include_edges: bool = False


@dataclass
class GetWorkflowNodeTreeResult:
    workflow_id: str = ""
    workflow_name: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    max_depth_reached: bool = False
    total_nodes: int = 0
    total_workflows_resolved: int = 0
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class GetWorkflowNodeTreeUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: GetWorkflowNodeTreeCommand) -> GetWorkflowNodeTreeResult:
        self.logger.info("Getting workflow node tree", id=command.workflow_id)
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return GetWorkflowNodeTreeResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        max_depth = min(command.max_depth, 10)
        visited: set[str] = set()
        ctx = _ResolveContext()

        nodes = await self._resolve_nodes(
            workflow, 0, max_depth, command.include_edges, visited, ctx,
        )
        edges = _edges_to_dicts(workflow) if command.include_edges else []

        return GetWorkflowNodeTreeResult(
            workflow_id=workflow.id,
            workflow_name=workflow.name,
            nodes=nodes,
            edges=edges,
            max_depth_reached=ctx.max_depth_reached,
            total_nodes=ctx.total_nodes,
            total_workflows_resolved=ctx.total_workflows_resolved,
        )

    async def _resolve_nodes(
        self,
        workflow: Workflow,
        depth: int,
        max_depth: int,
        include_edges: bool,
        visited: set[str],
        ctx: "_ResolveContext",
    ) -> list[dict[str, Any]]:
        visited.add(workflow.id)
        result: list[dict[str, Any]] = []

        for node in workflow.nodes:
            node_dict = _node_to_dict(node, depth, workflow.id)
            ctx.total_nodes += 1

            if node.node_type == NodeType.WORKFLOW:
                child_wf_id = node.config.get("workflow_id")
                if child_wf_id and child_wf_id not in visited and depth < max_depth:
                    child_wf = await self.workflow_repo.aget_by_id(child_wf_id)
                    if child_wf:
                        ctx.total_workflows_resolved += 1
                        child_nodes = await self._resolve_nodes(
                            child_wf, depth + 1, max_depth, include_edges, visited, ctx,
                        )
                        child_edges = _edges_to_dicts(child_wf) if include_edges else []
                        node_dict["children"] = {
                            "workflow_id": child_wf.id,
                            "workflow_name": child_wf.name,
                            "nodes": child_nodes,
                            "edges": child_edges,
                        }
                elif child_wf_id and depth >= max_depth:
                    ctx.max_depth_reached = True

            result.append(node_dict)

        return result


@dataclass
class _ResolveContext:
    total_nodes: int = 0
    total_workflows_resolved: int = 0
    max_depth_reached: bool = False


def _node_to_dict(node: WorkflowNode, depth: int, parent_workflow_id: str) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "node_type": node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type),
        "worker_type": node.worker_type,
        "parameters": merge_parameters(node.config, node.input_binding),
        "x": node.x,
        "y": node.y,
        "node_class": node.node_class,
        "description": node.description,
        "instruction": node.instruction,
        "icon": node.icon,
        "color": node.color,
        "tags": node.tags,
        "ports": node.ports,
        "node_definition_id": node.node_definition_id,
        "editable": node.editable,
        "depth": depth,
        "parent_workflow_id": parent_workflow_id,
        "children": None,
    }


def _edges_to_dicts(workflow: Workflow) -> list[dict[str, Any]]:
    return [
        {
            "id": e.id,
            "source_node_id": e.source_node_id,
            "target_node_id": e.target_node_id,
            "condition": e.condition,
            "source_port_id": e.source_port_id,
            "target_port_id": e.target_port_id,
            "label": e.label,
        }
        for e in workflow.edges
    ]
