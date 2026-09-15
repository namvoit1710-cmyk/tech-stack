"""Create a new workflow definition."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid
from typing import Any

from typing import Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.worker_registry_client_interface import IWorkerRegistryClient
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.workflow_snapshot_repository_interface import IWorkflowSnapshotRepository
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.repositories.run_repository_interface import IRunRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.node_spec.parameter_mapper import merge_parameters
from app.layer2_application.definition.node_config_validator import check_workflow_config
from app.layer2_application.definition.node_spec_resolver import NodeSpecResolver
from app.layer1_domain.entities.workflow_run import WorkflowRun, RunContext
from app.layer1_domain.value_objects.run_status import RunStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.agent_ref import AgentRef
from app.layer1_domain.entities.workflow_run import GraphTopology
from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
from app.layer2_application.definition.workflow_snapshot_loader import capture_snapshot
from app.layer2_application.definition.workflow_version_pinner import pin_spec_versions
from app.layer2_application.definition.concurrency_ports import wire_concurrency_ports


def _set_builtin_worker_types(workflow: Workflow) -> None:
    """Set worker_type for builtin nodes and default ports for all nodes with empty ports."""
    from app.layer1_domain.node_spec import get_spec
    for node in workflow.nodes:
        # Set worker_type for non-TASK nodes
        if node.node_type != NodeType.TASK and not node.worker_type:
            node_type_val = node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type)
            node.worker_type = f"builtin:{node_type_val.lower()}"
        # Set default ports from the node spec if empty (all node types)
        has_ports = (node.ports.get("in") or node.ports.get("out")) if isinstance(node.ports, dict) else False
        if not has_ports:
            spec = get_spec(node.node_type)
            node.in_ports = [p.to_dict() for p in spec.in_ports]
            node.out_ports = [p.to_dict() for p in spec.out_ports]
            node.ports = {"in": node.in_ports, "out": node.out_ports}


@dataclass
class CreateWorkflowCommand:
    name: str = ""
    description: str = ""
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    routing_path: str = "/"
    schema_version: str = "1.0.0"
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = field(default_factory=list)
    # SA-2570: the authenticated caller (from the verified identity), used as
    # the immutable creator and the default owner. Empty falls back to "system".
    created_by: str = ""
    owner: str = ""
    updated_by: str = ""



@dataclass
class CreateWorkflowResult:
    id: str = ""
    name: str = ""
    version: int = 0
    description: str = ""
    status: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    schema_version: str = "1.0.0"
    routing_path: str = "/"
    test_run_id: str | None = None
    main_flow: bool = False
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = field(default_factory=list)
    created_by: str = ""
    updated_by: str = ""
    owner: str | None = None



class CreateWorkflowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository,
                 node_definition_repo: Optional[INodeDefinitionRepository] = None,
                 node_definition_resolver: Optional[NodeDefinitionResolver] = None,
                 run_repo: Optional[IRunRepository] = None,
                 workflow_snapshot_repo: Optional[IWorkflowSnapshotRepository] = None,
                 worker_registry_client: Optional[IWorkerRegistryClient] = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.node_definition_repo = node_definition_repo
        self.resolver = node_definition_resolver or NodeDefinitionResolver()
        self.run_repo = run_repo
        self.workflow_snapshot_repo = workflow_snapshot_repo
        self.worker_registry_client = worker_registry_client

    async def execute(self, command: CreateWorkflowCommand) -> CreateWorkflowResult:
        self.logger.info("Creating workflow", name=command.name)

        # Validate workflow name is not empty/whitespace
        if not command.name or not command.name.strip():
            return CreateWorkflowResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "name"},
            )

        # Build nodes with validation
        try:
            nodes = [WorkflowNode(**n) for n in command.nodes]
        except Exception as e:
            return CreateWorkflowResult(
                error=ErrorCode.INVALID_NODE_DATA.default_message,
                error_code=ErrorCode.INVALID_NODE_DATA.code,
                error_details={"reason": str(e)},
            )

        # Build edges with validation
        try:
            edges = [WorkflowEdge(**e) for e in command.edges]
            # Deduplicate edges by (source_node_id, source_port_id, target_node_id, target_port_id)
            # Keep only the first occurrence of each unique edge
            seen = set()
            unique_edges = []
            for edge in edges:
                key = (edge.source_node_id, edge.source_port_id or "", edge.target_node_id, edge.target_port_id or "")
                if key not in seen:
                    seen.add(key)
                    unique_edges.append(edge)
            edges = unique_edges
        except Exception as e:
            return CreateWorkflowResult(
                error=ErrorCode.INVALID_EDGE_DATA.default_message,
                error_code=ErrorCode.INVALID_EDGE_DATA.code,
                error_details={"reason": str(e)},
            )

        # Validate edges reference existing node IDs
        node_ids = {n.id for n in nodes}
        for edge in edges:
            if edge.source_node_id not in node_ids:
                return CreateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.source_node_id},
                )
            if edge.target_node_id not in node_ids:
                return CreateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.target_node_id},
                )

        workflow = Workflow(
            routing_path=command.routing_path,
            name=command.name,
            description=command.description,
            input_schema=command.input_schema,
            output_schema=command.output_schema,
            nodes=nodes,
            edges=edges,
            metadata=command.metadata,
            schema_version=command.schema_version,
            action=command.action,
            instruction=command.instruction,
            agents=command.agents,
        )

        # SA-2570: attribute the workflow to the authenticated caller. The
        # creator is immutable; the owner defaults to the creator and is
        # reassignable later. Empty (auth off) falls back to "system".
        actor = command.created_by or "system"
        workflow.created_by = actor
        workflow.updated_by = command.updated_by or actor
        workflow.owner = command.owner or actor

        # Spec-driven node-config validation (NodeSpec Phase 2). Runs
        # before enrichment so the smuggled `_node_definition` snapshot
        # never reaches the validator. Mode via NODE_CONFIG_VALIDATION.
        blocking = check_workflow_config(
            workflow, self.logger,
            spec_resolver=NodeSpecResolver(
                node_definition_repo=self.node_definition_repo,
                worker_registry_client=self.worker_registry_client,
            ),
        )
        if blocking:
            return CreateWorkflowResult(
                error=ErrorCode.NODE_CONFIG_INVALID.default_message,
                error_code=ErrorCode.NODE_CONFIG_INVALID.code,
                error_details={"violations": [v.to_dict() for v in blocking]},
            )

        try:
            self._enrich_nodes_with_definitions(workflow)
        except Exception as e:
            return CreateWorkflowResult(
                error=ErrorCode.ENRICHMENT_FAILED.default_message,
                error_code=ErrorCode.ENRICHMENT_FAILED.code,
                error_details={"reason": str(e)},
            )

        _set_builtin_worker_types(workflow)
        # MERGE/PARALLEL route by edge but need a declared port per branch so the FE
        # doesn't drop the fan-in/out edges on reconcile (see concurrency_ports docstring).
        wire_concurrency_ports(workflow)
        # Pin spec_versions at save time — the version stored with the
        # workflow is what the engine uses at every future dispatch,
        # unless the workflow is resaved or the version is changed.
        pin_spec_versions(
            workflow,
            resolver=self.resolver,
            worker_registry_client=self.worker_registry_client,
            node_definition_repo=self.node_definition_repo,
        )
        await self.workflow_repo.asave(workflow)

        try:
            await self._create_test_run(workflow)
        except Exception as e:
            self.logger.error("Test run creation failed (non-fatal)", error=str(e))

        return _to_result(workflow)

    async def _create_test_run(self, workflow: Workflow) -> None:
        if not self.run_repo:
            return
        topology = GraphTopology.from_workflow(workflow)
        # Capture a snapshot so the engine can resolve the workflow on any
        # later callback (PATCH /data, POST /submit, etc.). Without this,
        # load_workflow_for_run() raises WorkflowSnapshotMissingError.
        snapshot_id = ""
        if self.workflow_snapshot_repo is not None:
            snapshot_id = capture_snapshot(workflow, self.workflow_snapshot_repo)
        test_run = WorkflowRun(
            workflow_id=workflow.id,
            workflow_version=workflow.version,
            workflow_snapshot_id=snapshot_id,
            status=RunStatus.CREATED,
            is_test=True,
            context=RunContext(variables={"artifacts": {}}, topology=topology),
        )
        await self.run_repo.asave(test_run)
        workflow.test_run_id = test_run.id
        await self.workflow_repo.asave(workflow)

    def _enrich_nodes_with_definitions(self, workflow: Workflow) -> None:
        if not self.node_definition_repo:
            return
        for node in workflow.nodes:
            # Accept the definition reference via an explicit node_definition_id
            # or an implicit ``business:<def-id>`` worker_type (the FE palette
            # catalog id). See UpdateWorkflowUseCase for the full rationale.
            nd_id = node.node_definition_id
            if not nd_id and node.worker_type and node.worker_type.startswith("business:"):
                nd_id = node.worker_type.removeprefix("business:")
                node.node_definition_id = nd_id
            if not nd_id:
                continue
            resolved = self.resolver.resolve(nd_id, self.node_definition_repo)
            # NodeSpec Phase 4: the resolution artifact lives on its own
            # entity field instead of being smuggled into config under
            # underscore keys (one merged dict — the old _node_definition
            # and _node_internals halves were always written together).
            node.definition_snapshot = {
                "input_schema": resolved.input_schema,
                "output_schema": resolved.output_schema,
                "data_mode": resolved.data_mode,
                "edit_mode": resolved.edit_mode,
                "locked_defaults": resolved.locked_defaults,
                "input_mapping": resolved.input_mapping,
                "output_mapping": resolved.output_mapping,
                "base_worker_id": resolved.base_worker_id,
            }
            # Normalize a catalog-id worker_type (``business:``/``builtin:``)
            # to the runnable base worker the executor understands.
            if resolved.base_worker_id and (
                not node.worker_type or ":" in node.worker_type
            ):
                node.worker_type = resolved.base_worker_id
            if resolved.ports:
                node.ports = resolved.ports
            node.node_class = "BUSINESS"



def _serialize_node_agents(agents: list[AgentRef]) -> list[dict[str, Any]]:
    """Convert list of AgentRef to list of dicts for API response."""
    return [
        a.to_dict() if isinstance(a, AgentRef) else a
        for a in agents
    ]


def _to_result(workflow: Workflow) -> CreateWorkflowResult:
    return CreateWorkflowResult(
        id=workflow.id,
        name=workflow.name,
        version=workflow.version,
        description=workflow.description,
        status=workflow.status.value,
        nodes=[
            {
                "id": n.id, "name": n.name,
                "node_type": n.node_type.value if hasattr(n.node_type, 'value') else str(n.node_type),
                "worker_type": n.worker_type,
                "spec_version": n.spec_version,
                "parameters": merge_parameters(n.config, n.input_binding),
                "x": n.x,
                "y": n.y,
                "node_class": n.node_class,
                "description": n.description,
                "icon": n.icon,
                "color": n.color,
                "tags": n.tags,
                "ports": n.ports,
                "node_definition_id": n.node_definition_id,
                "editable": n.editable,
                "action": n.action,
                "instruction": n.instruction,
                "agents": _serialize_node_agents(n.agents),
            }
            for n in workflow.nodes
        ],
        edges=[
            {
                "id": e.id, "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id, "condition": e.condition,
                "source_port_id": e.source_port_id,
                "target_port_id": e.target_port_id,
                "label": e.label,
            }
            for e in workflow.edges
        ],
        input_schema=workflow.input_schema,
        output_schema=workflow.output_schema,
        metadata=workflow.metadata,
        created_at=workflow.created_at,
        schema_version=workflow.schema_version,
        test_run_id=workflow.test_run_id,
        main_flow=workflow.main_flow,
        action=workflow.action,
        instruction=workflow.instruction,
        agents=_serialize_node_agents(workflow.agents),
        created_by=workflow.created_by or "",
        updated_by=workflow.updated_by or "",
        owner=workflow.owner or None,
    )
