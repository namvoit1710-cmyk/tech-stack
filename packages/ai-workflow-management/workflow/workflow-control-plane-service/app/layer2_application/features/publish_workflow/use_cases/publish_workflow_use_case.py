"""Publish a workflow (DRAFT → PUBLISHED)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.node_spec.parameter_mapper import merge_parameters
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.value_objects.agent_ref import AgentRef


@dataclass
class PublishWorkflowCommand:
    workflow_id: str = ""
    version: str = ""
    is_confirm: bool = False


@dataclass
class PublishWorkflowResult:
    id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    status: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    schema_version: str = "1.0.0"
    test_run_id: str | None = None
    routing_path: str | None = None
    main_flow: bool = False
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class PublishWorkflowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo

    async def execute(self, command: PublishWorkflowCommand) -> PublishWorkflowResult:
        self.logger.info(f"Publishing workflow {command.workflow_id} with version {command.version}")

        workflow = await self.workflow_repo.aget_by_id_version(command.workflow_id, command.version)
        if not workflow:
            return PublishWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        # Already published — nothing to do
        if workflow.status == WorkflowStatus.PUBLISHED:
            return _to_result(workflow)

        # Validate structure
        validation_error = self._validate_structure(workflow)
        if validation_error:
            return validation_error

        # Check if another version of this workflow is already published
        existing_published = await self.workflow_repo.aget_list_version_by_status(command.workflow_id, command.version, WorkflowStatus.PUBLISHED.value)
        if existing_published and not command.is_confirm:
            return PublishWorkflowResult(
                error=ErrorCode.WORKFLOW_ALREADY_PUBLISHED.default_message,
                error_code=ErrorCode.WORKFLOW_ALREADY_PUBLISHED.code,
                error_details={
                    "workflow_id": command.workflow_id,
                    "published_version_id": existing_published[0].id,
                },
            )

        # SA-2252 — ORDER MATTERS. Demote the previously-published version(s) to
        # DRAFT *first*, then save the newly-published version *last*, so the
        # published row is the most-recently-saved and carries the highest
        # REVISION. `_pick_current` / the list's latest-edited window resolve
        # "current" by highest REVISION, so if the demote-to-DRAFT save ran last
        # the demoted (now-DRAFT) old version became "current" and the list showed
        # the workflow as DRAFT immediately after a re-publish. (The repo may
        # legitimately return an empty list / None when nothing was published
        # before.)
        for published_version in existing_published or []:
            published_version.status = WorkflowStatus.DRAFT
            # existing_published.updated_at = datetime.now(timezone.utc).isoformat()
            await self.workflow_repo.asave(published_version)

        # Publish current version last → highest REVISION → the current row.
        workflow.status = WorkflowStatus.PUBLISHED
        workflow.updated_at = datetime.now(timezone.utc).isoformat()
        await self.workflow_repo.asave(workflow)

        return _to_result(workflow)

    def _validate_structure(self, workflow: Workflow) -> PublishWorkflowResult | None:
        """Validate workflow has at least 1 node and edges reference valid node IDs."""
        if not workflow.nodes:
            return PublishWorkflowResult(
                error=ErrorCode.INVALID_WORKFLOW.default_message,
                error_code=ErrorCode.INVALID_WORKFLOW.code,
                error_details={"reason": "Workflow must have at least one node to publish"},
            )

        node_ids = {n.id for n in workflow.nodes}
        for edge in workflow.edges:
            if edge.source_node_id not in node_ids:
                return PublishWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.source_node_id},
                )
            if edge.target_node_id not in node_ids:
                return PublishWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.target_node_id},
                )

        return None


def _serialize_node_agents(agents: list[AgentRef]) -> list[dict[str, Any]]:
    return [
        a.to_dict() if isinstance(a, AgentRef) else a
        for a in agents
    ]


def _to_result(workflow: Workflow) -> PublishWorkflowResult:
    return PublishWorkflowResult(
        id=workflow.id, name=workflow.name, version=workflow.version,
        description=workflow.description, status=workflow.status.value,
        nodes=[{
            "id": n.id, "name": n.name,
            "node_type": n.node_type.value if hasattr(n.node_type, 'value') else str(n.node_type),
            "worker_type": n.worker_type,
            "spec_version": n.spec_version,
            "parameters": merge_parameters(n.config, n.input_binding),
            "x": n.x, "y": n.y,
            "node_class": n.node_class, "description": n.description,
            "icon": n.icon, "color": n.color, "tags": n.tags,
            "ports": n.ports,
            "node_definition_id": n.node_definition_id,
            "editable": n.editable,
            "action": n.action,
            "instruction": n.instruction,
            "agents": _serialize_node_agents(n.agents),
        } for n in workflow.nodes],
        edges=[{
            "id": e.id, "source_node_id": e.source_node_id,
            "target_node_id": e.target_node_id, "condition": e.condition,
            "source_port_id": e.source_port_id, "target_port_id": e.target_port_id,
            "label": e.label,
        } for e in workflow.edges],
        input_schema=workflow.input_schema, output_schema=workflow.output_schema,
        metadata=workflow.metadata, created_at=workflow.created_at,
        schema_version=workflow.schema_version,
        test_run_id=workflow.test_run_id,
        routing_path=workflow.routing_path,
        main_flow=workflow.main_flow,
        action=workflow.action,
        instruction=workflow.instruction,
        agents=_serialize_node_agents(workflow.agents),
    )
