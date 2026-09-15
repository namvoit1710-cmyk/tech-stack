"""Update an existing workflow (full replacement and partial patch)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from typing import Optional

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.interfaces.worker_registry_client_interface import IWorkerRegistryClient
from app.layer2_application.repositories.workflow_repository_interface import IWorkflowRepository
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.workflow import Workflow, WorkflowNode, WorkflowEdge
from app.layer1_domain.node_spec.parameter_mapper import merge_parameters
from app.layer2_application.definition.node_config_validator import check_workflow_config
from app.layer2_application.definition.node_spec_resolver import NodeSpecResolver
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer1_domain.value_objects.agent_ref import AgentRef
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
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


# -- Commands ----------------------------------------------------------------

@dataclass
class ReplaceWorkflowCommand:
    """Full replacement (PUT). All mutable fields are required."""
    workflow_id: str = ""
    name: str = ""
    version: str = ""
    description: str = ""
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0.0"
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = field(default_factory=list)
    is_confirm: bool = False
    updated_by: str = ""  # SA-2570: the authenticated caller stamps updated_by


@dataclass
class PatchWorkflowCommand:
    """Partial update (PATCH). Only non-None fields are applied."""
    workflow_id: str = ""
    name: str | None = None
    version: str = ""
    description: str | None = None
    input_schema: list[dict[str, Any]] | None = None
    output_schema: list[dict[str, Any]] | None = None
    nodes: list[dict[str, Any]] | None = None
    edges: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    schema_version: str | None = None
    routing_path: str | None = None
    main_flow: bool | None = None
    action: str | None = None
    instruction: str | None = None
    agents: list[dict[str, Any]] | None = None
    is_confime: bool = False
    updated_by: str = ""  # SA-2570: the authenticated caller stamps updated_by


# Keep backward-compatible alias used by existing imports
UpdateWorkflowCommand = PatchWorkflowCommand


# -- Result ------------------------------------------------------------------

@dataclass
class UpdateWorkflowResult:
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
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0.0"
    test_run_id: str | None = None
    main_flow: bool = False
    action: str = ""
    instruction: str = ""
    agents: list[dict[str, Any]] = field(default_factory=list)
    created_by: str = ""
    updated_by: str = ""
    owner: str | None = None


# -- Use Case ----------------------------------------------------------------

class UpdateWorkflowUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository,
                 node_definition_repo: Optional[INodeDefinitionRepository] = None,
                 node_definition_resolver: Optional[NodeDefinitionResolver] = None,
                 worker_registry_client: Optional[IWorkerRegistryClient] = None,
                 **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        self.node_definition_repo = node_definition_repo
        self.resolver = node_definition_resolver or NodeDefinitionResolver()
        self.worker_registry_client = worker_registry_client

    async def execute(self, command: PatchWorkflowCommand | ReplaceWorkflowCommand) -> UpdateWorkflowResult:
        self.logger.info("Updating workflow", id=command.workflow_id)
        workflow = await self.workflow_repo.aget_by_id(command.workflow_id)
        if not workflow:
            return UpdateWorkflowResult(
                error=ErrorCode.WORKFLOW_NOT_FOUND.default_message,
                error_code=ErrorCode.WORKFLOW_NOT_FOUND.code,
                error_details={"workflow_id": command.workflow_id},
            )

        should_create_new_version = False

        # ── Optimistic Concurrency Control (version check) ──
        if isinstance(command, ReplaceWorkflowCommand) and command.version is not None:
            latest_version = str(workflow.version)
            if latest_version is not None:
                if latest_version != command.version and not command.is_confirm:
                    # Version is stale and not confirmed and changed fields exist
                    # Return conflict error
                    changed_fields = self._detect_changed_fields(workflow, command, ">")
                    if changed_fields:
                        return UpdateWorkflowResult(
                            error=ErrorCode.WORKFLOW_VERSION_CONFLICT.default_message,
                            error_code=ErrorCode.WORKFLOW_VERSION_CONFLICT.code,
                            error_details={
                                "id": command.workflow_id,
                                "version": command.version,
                                "latest_version": latest_version,
                                "changed_fields": changed_fields,
                            },
                        )

                # Create new version in two cases:
                # 1. Version is stale (different) AND confirmed
                # 2. Version is same BUT there are actual changes
                if (latest_version != command.version and command.is_confirm) or \
                   (latest_version == command.version and self._detect_changed_fields(workflow, command, "=")):
                    should_create_new_version = True

        if isinstance(command, ReplaceWorkflowCommand):
            error_result = self._apply_replace(workflow, command)
        else:
            error_result = self._apply_patch(workflow, command)

        if error_result is not None:
            return error_result

        # Spec-driven node-config validation (NodeSpec Phase 2) — same
        # placement as create: post node construction, pre enrichment.
        blocking = check_workflow_config(
            workflow, self.logger,
            spec_resolver=NodeSpecResolver(
                node_definition_repo=self.node_definition_repo,
                worker_registry_client=self.worker_registry_client,
            ),
        )
        if blocking:
            return UpdateWorkflowResult(
                error=ErrorCode.NODE_CONFIG_INVALID.default_message,
                error_code=ErrorCode.NODE_CONFIG_INVALID.code,
                error_details={"violations": [v.to_dict() for v in blocking]},
            )

        try:
            self._enrich_nodes_with_definitions(workflow)
        except Exception as e:
            return UpdateWorkflowResult(
                error=ErrorCode.ENRICHMENT_FAILED.default_message,
                error_code=ErrorCode.ENRICHMENT_FAILED.code,
                error_details={"reason": str(e)},
            )

        _set_builtin_worker_types(workflow)
        # MERGE/PARALLEL route by edge but need a declared port per branch so the FE
        # doesn't drop the fan-in/out edges on reconcile (see concurrency_ports docstring).
        wire_concurrency_ports(workflow)
        # Pin spec_versions at save time — same rule as create.
        # Existing pinned values are preserved; None → resolve to current
        # latest and freeze into the workflow JSON.
        pin_spec_versions(
            workflow,
            resolver=self.resolver,
            worker_registry_client=self.worker_registry_client,
            node_definition_repo=self.node_definition_repo,
        )

        # ── Create new version record (if versioning is active) ──
        if isinstance(command, ReplaceWorkflowCommand) and workflow.workflow_id and should_create_new_version:
            import uuid
            workflow.version = str(uuid.uuid4())
            workflow.id = workflow.id
            workflow.workflow_id = str(uuid.uuid4())
            workflow.created_at = datetime.now(timezone.utc).isoformat()
            workflow.status = WorkflowStatus.DRAFT

        workflow.updated_at = datetime.now(timezone.utc).isoformat()
        # SA-2570: stamp the authenticated caller as the last editor. Preserve the
        # existing value when auth is off (empty command.updated_by).
        if command.updated_by:
            workflow.updated_by = command.updated_by
        await self.workflow_repo.asave(workflow)
        return _to_result(workflow)

    def _apply_replace(self, workflow: Workflow, cmd: ReplaceWorkflowCommand) -> UpdateWorkflowResult | None:
        """Full replacement — overwrites every mutable field. Returns error result or None."""
        if not cmd.name or not cmd.name.strip():
            return UpdateWorkflowResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"field": "name"},
            )

        workflow.name = cmd.name
        workflow.description = cmd.description
        workflow.input_schema = cmd.input_schema
        workflow.output_schema = cmd.output_schema

        try:
            workflow.nodes = [WorkflowNode(**n) for n in cmd.nodes]
        except Exception as e:
            return UpdateWorkflowResult(
                error=ErrorCode.INVALID_NODE_DATA.default_message,
                error_code=ErrorCode.INVALID_NODE_DATA.code,
                error_details={"reason": str(e)},
            )

        try:
            workflow.edges = [WorkflowEdge(**e) for e in cmd.edges]
            # Deduplicate edges by (source_node_id, source_port_id, target_node_id, target_port_id)
            # Keep only the first occurrence of each unique edge
            seen = set()
            unique_edges = []
            for edge in workflow.edges:
                key = (edge.source_node_id, edge.source_port_id or "", edge.target_node_id, edge.target_port_id or "")
                if key not in seen:
                    seen.add(key)
                    unique_edges.append(edge)
            workflow.edges = unique_edges
        except Exception as e:
            return UpdateWorkflowResult(
                error=ErrorCode.INVALID_EDGE_DATA.default_message,
                error_code=ErrorCode.INVALID_EDGE_DATA.code,
                error_details={"reason": str(e)},
            )

        # Validate edges reference existing node IDs
        node_ids = {n.id for n in workflow.nodes}
        for edge in workflow.edges:
            if edge.source_node_id not in node_ids:
                return UpdateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.source_node_id},
                )
            if edge.target_node_id not in node_ids:
                return UpdateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.target_node_id},
                )

        workflow.metadata = cmd.metadata
        workflow.schema_version = cmd.schema_version
        workflow.action = cmd.action
        workflow.instruction = cmd.instruction
        workflow.agents = cmd.agents
        return None

    def _apply_patch(self, workflow: Workflow, cmd: PatchWorkflowCommand) -> UpdateWorkflowResult | None:
        """Partial update — only non-None fields are applied. Returns error result or None."""
        if cmd.name is not None:
            if not cmd.name.strip():
                return UpdateWorkflowResult(
                    error=ErrorCode.VALIDATION_ERROR.default_message,
                    error_code=ErrorCode.VALIDATION_ERROR.code,
                    error_details={"field": "name"},
                )
            workflow.name = cmd.name
        if cmd.description is not None:
            workflow.description = cmd.description
        if cmd.input_schema is not None:
            workflow.input_schema = cmd.input_schema
        if cmd.output_schema is not None:
            workflow.output_schema = cmd.output_schema
        if cmd.routing_path is not None:
            workflow.routing_path = cmd.routing_path
        if cmd.nodes is not None:
            try:
                workflow.nodes = [WorkflowNode(**n) for n in cmd.nodes]
            except Exception as e:
                return UpdateWorkflowResult(
                    error=ErrorCode.INVALID_NODE_DATA.default_message,
                    error_code=ErrorCode.INVALID_NODE_DATA.code,
                    error_details={"reason": str(e)},
                )
        if cmd.edges is not None:
            try:
                workflow.edges = [WorkflowEdge(**e) for e in cmd.edges]
                # Deduplicate edges by (source_node_id, source_port_id, target_node_id, target_port_id)
                # Keep only the first occurrence of each unique edge
                seen = set()
                unique_edges = []
                for edge in workflow.edges:
                    key = (edge.source_node_id, edge.source_port_id or "", edge.target_node_id, edge.target_port_id or "")
                    if key not in seen:
                        seen.add(key)
                        unique_edges.append(edge)
                workflow.edges = unique_edges
            except Exception as e:
                return UpdateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_DATA.default_message,
                    error_code=ErrorCode.INVALID_EDGE_DATA.code,
                    error_details={"reason": str(e)},
                )

        # Validate edges reference existing node IDs (after nodes may have been updated)
        node_ids = {n.id for n in workflow.nodes}
        for edge in workflow.edges:
            if edge.source_node_id not in node_ids:
                return UpdateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.source_node_id},
                )
            if edge.target_node_id not in node_ids:
                return UpdateWorkflowResult(
                    error=ErrorCode.INVALID_EDGE_REFERENCE.default_message,
                    error_code=ErrorCode.INVALID_EDGE_REFERENCE.code,
                    error_details={"edge_id": edge.id, "node_id": edge.target_node_id},
                )

        if cmd.metadata is not None:
            workflow.metadata = cmd.metadata
        if cmd.schema_version is not None:
            workflow.schema_version = cmd.schema_version
        if cmd.main_flow is not None:
            workflow.main_flow = cmd.main_flow
        if cmd.action is not None:
            workflow.action = cmd.action
        if cmd.instruction is not None:
            workflow.instruction = cmd.instruction
        if cmd.agents is not None:
            workflow.agents = cmd.agents
        return None

    def _enrich_nodes_with_definitions(self, workflow: Workflow) -> None:
        if not self.node_definition_repo:
            return
        for node in workflow.nodes:
            # A node may carry the business definition either explicitly via
            # ``node_definition_id`` or only implicitly through a
            # ``business:<def-id>`` worker_type (the palette catalog id the FE
            # drops onto the canvas). Derive the id from the latter so the node
            # still gets enriched — without this the definition is never
            # resolved, ``definition_snapshot`` stays empty, the locked
            # ``code``/``language`` defaults are never injected, and the task
            # dispatches to the unroutable ``business:<id>`` worker.
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
            # Normalize the worker_type to the resolved base worker (e.g.
            # "code"). A ``business:``/``builtin:``-prefixed worker_type is a
            # catalog id, not a runnable worker type — the executor keys on the
            # bare type — so overwrite it as well as the empty case.
            if resolved.base_worker_id and (
                not node.worker_type or ":" in node.worker_type
            ):
                node.worker_type = resolved.base_worker_id
            if resolved.ports:
                node.ports = resolved.ports
            node.node_class = "BUSINESS"

    def _detect_changed_fields(self, latest_workflow: Workflow, command: ReplaceWorkflowCommand, condition: str = ">") -> list[str]:
        """Return list of top-level field names that differ between
        the base_workflow (the version client was editing) and the latest
        version in the same group."""
        import dataclasses
        from app.layer2_application.definition.workflow_change_detector import compare_changes

        base_workflow: Workflow | ReplaceWorkflowCommand = command #default for case equal version

        if condition == ">":
            base_workflow = self.workflow_repo.get_by_id_version(command.workflow_id, command.version)
        
        if not base_workflow:
            return []

        compare_keys = {
            "name", "description", "nodes", "edges", "input_schema",
            "output_schema", "metadata", "action", "instruction"
        }
        base_dict = dataclasses.asdict(base_workflow)
        latest_dict = dataclasses.asdict(latest_workflow)

        changed: set[str] = set()
        for key in compare_keys:
            diffs = list(compare_changes(base_dict.get(key), latest_dict.get(key)))
            diffs = self.re_check_diffs(key, diffs)
            if diffs:
                changed.add(key)
        return sorted(changed)

    def re_check_diffs(self, key: str, diffs: list) -> list:
        results: list = []
        for attribute, value_1, value_2 in diffs:
            if 'node_type' in attribute:
                if value_1 == value_2.value: # Node type should compare by .value
                    continue
            if key == "edges" and "id" in attribute:
                continue
            results.append((attribute, value_1, value_2))
        return results

def _serialize_node_agents(agents: list[AgentRef]) -> list[dict[str, Any]]:
    """Convert list of AgentRef to list of dicts for API response."""
    return [
        a.to_dict() if isinstance(a, AgentRef) else a
        for a in agents
    ]


def _to_result(workflow: Workflow) -> UpdateWorkflowResult:
    return UpdateWorkflowResult(
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
        main_flow=workflow.main_flow,
        action=workflow.action,
        instruction=workflow.instruction,
        agents=_serialize_node_agents(workflow.agents),
        created_by=workflow.created_by or "",
        updated_by=workflow.updated_by or "",
        owner=workflow.owner or None,
    )
