"""Get a workflow by ID."""

import json
from dataclasses import dataclass, field
from typing import Any, Optional
import uuid

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.node_spec.parameter_mapper import merge_parameters


@dataclass
class GetWorkflowCommand:
    workflow_id: str = ""
    version: Optional[str] = ""


@dataclass
class GetWorkflowByRoutingPathCommand:
    routing_path: str = ""



@dataclass
class GetWorkflowResult:
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    status: str = ""
    created_by: str = ""
    updated_by: str = ""
    owner: str | None = None
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    test_run_id: str | None = None
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    routing_path: str = "/"
    main_flow: bool = False
    all_input_schema: list = field(default_factory=list)



class GetWorkflowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: GetWorkflowCommand, is_publish: bool = False) -> GetWorkflowResult:
        self.logger.info("Getting workflow", id=command.workflow_id)

        if is_publish:
            workflow = self.workflow_repo.get_by_latest_publish_version(command.workflow_id)
        elif command.version:
            workflow = await self.workflow_repo.aget_by_id_version(command.workflow_id, command.version)
        else:
            workflow = await self.workflow_repo.aget_by_id(command.workflow_id)

        if not workflow:
            return GetWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
            )

        child_workflows = self.workflow_repo.get_all_child_workflows(workflow.workflow_id) #(command.workflow_id)
        
        child_nodes = [
            node
            for child in child_workflows
            for node in child.get("nodes", []) if node.get("node_type", "") == "INPUT"
        ]
        combined_schema: list = []
        for node in child_nodes:
            config = node.get("config")

            if isinstance(config, dict) and "input_schema" in config:
                input_schema = config.get("input_schema", "")
                if not input_schema:
                    continue
                json_schema = json.loads(input_schema)
                if isinstance(json_schema, dict):
                    json_schema = [json_schema]

                for item in json_schema:
                    item["node_id"] = node.get("id", "")

                combined_schema.append(json_schema)
        return _to_detail_result(workflow, all_input_schema=combined_schema)

    async def get_by_routing_path(self, command: GetWorkflowByRoutingPathCommand) -> GetWorkflowResult:
        self.logger.info("Getting workflow by routing_path", routing_path=command.routing_path)
        workflow = self.workflow_repo.get_by_routing_path(command.routing_path)
        if not workflow:
            return GetWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"routing_path": command.routing_path},
            )
        return _to_result(workflow)



def _to_result(workflow: Workflow) -> GetWorkflowResult:
    return GetWorkflowResult(
        id=workflow.id,
        name=workflow.name,
        version=workflow.version,
        description=workflow.description, status=workflow.status.value,
        nodes=[{
            "id": n.id, "name": n.name,
            "node_type": n.node_type.value if hasattr(n.node_type, 'value') else str(n.node_type),
            "worker_type": n.worker_type,
            "parameters": merge_parameters(n.config, n.input_binding),
            "x": n.x, "y": n.y,
            "node_class": n.node_class, "description": n.description,
            "instruction": n.instruction,
            "icon": n.icon, "color": n.color, "tags": n.tags,
            "ports": n.ports,
            "node_definition_id": n.node_definition_id,
            "editable": n.editable,
        } for n in workflow.nodes],
        edges=[{
            "id": e.id, "source_node_id": e.source_node_id,
            "target_node_id": e.target_node_id, "condition": e.condition,
            "source_port_id": e.source_port_id, "target_port_id": e.target_port_id,
            "label": e.label,
        } for e in workflow.edges],
        input_schema=workflow.input_schema, output_schema=workflow.output_schema,
        metadata=workflow.metadata, created_at=workflow.created_at,
        test_run_id=workflow.test_run_id,
        routing_path=workflow.routing_path,
        main_flow=workflow.main_flow,
        created_by=workflow.created_by or "",
        updated_by=workflow.updated_by or "",
        owner=workflow.owner or None,
    )

def _to_detail_result(workflow: Workflow, all_input_schema: list | None = None) -> GetWorkflowResult:
    result = _to_result(workflow)
    result.all_input_schema = all_input_schema or []
    return result
