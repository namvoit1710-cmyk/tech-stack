"""Create a new node definition (business node template)."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.node_kind import resolve_kind
from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
from app.layer1_domain.exceptions import CircularInheritanceError, InheritanceDepthExceededError
from app.layer1_domain.node_spec import get_spec


@dataclass
class CreateNodeDefinitionCommand:
    tenant_id: str = "system"
    name: str = ""
    description: str = ""
    parent_definition_id: str | None = None
    base_type: str = "TASK"
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] = field(default_factory=dict)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    input_mapping: dict[str, Any] = field(default_factory=dict)
    output_mapping: dict[str, Any] = field(default_factory=dict)
    icon: str = ""
    color: str = ""
    tags: list[str] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)
    data_mode: str = "json"
    edit_mode: str = "none"
    kind: str = ""
    collection: str = ""


@dataclass
class CreateNodeDefinitionResult:
    id: str = ""
    tenant_id: str = ""
    name: str = ""
    description: str = ""
    parent_definition_id: str | None = None
    base_type: str = ""
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] = field(default_factory=dict)
    input_schema: list[dict[str, Any]] = field(default_factory=list)
    output_schema: list[dict[str, Any]] = field(default_factory=list)
    input_mapping: dict[str, Any] = field(default_factory=dict)
    output_mapping: dict[str, Any] = field(default_factory=dict)
    icon: str = ""
    color: str = ""
    tags: list[str] = field(default_factory=list)
    ports: dict[str, Any] = field(default_factory=dict)
    data_mode: str = "json"
    edit_mode: str = "none"
    kind: str = ""
    collection: str = ""
    created_at: str = ""
    updated_at: str = ""
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)


class CreateNodeDefinitionUseCase:
    def __init__(self, logger: ILogger, node_definition_repo: INodeDefinitionRepository,
                 node_definition_resolver: NodeDefinitionResolver | None = None, **kwargs: Any) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo
        self.resolver = node_definition_resolver or NodeDefinitionResolver()

    async def execute(self, command: CreateNodeDefinitionCommand) -> CreateNodeDefinitionResult:
        self.logger.info("Creating node definition", name=command.name)

        # Validate parent reference (keep the fetched parent for kind inheritance)
        parent = None
        if command.parent_definition_id:
            parent = self.node_definition_repo.get_by_id(command.parent_definition_id)
            if not parent:
                return CreateNodeDefinitionResult(
                    error=ErrorCode.PARENT_DEFINITION_NOT_FOUND.default_message,
                    error_code=ErrorCode.PARENT_DEFINITION_NOT_FOUND.code,
                )
            if parent.tenant_id != command.tenant_id:
                return CreateNodeDefinitionResult(
                    error=ErrorCode.TENANT_MISMATCH.default_message,
                    error_code=ErrorCode.TENANT_MISMATCH.code,
                )

        # F/SA-1740: parent-aware smart default — inherit parent's kind when the
        # user didn't set one (else the entity heals via pure default_kind).
        resolved_kind = resolve_kind(
            command.kind, parent, command.base_type, command.locked_defaults,
        )

        nd = NodeDefinition(
            tenant_id=command.tenant_id,
            name=command.name,
            description=command.description,
            parent_definition_id=command.parent_definition_id,
            base_type=command.base_type,
            base_worker_id=command.base_worker_id,
            locked_defaults=command.locked_defaults,
            input_schema=command.input_schema,
            output_schema=command.output_schema,
            input_mapping=command.input_mapping,
            output_mapping=command.output_mapping,
            icon=command.icon,
            color=command.color,
            tags=command.tags,
            ports=command.ports,
            data_mode=command.data_mode,
            edit_mode=command.edit_mode,
            kind=resolved_kind,
            collection=command.collection,
        )
        # Auto-populate ports from the node spec if not provided
        if not nd.ports or (not nd.ports.get("in") and not nd.ports.get("out")):
            spec = get_spec(nd.base_type)
            nd.ports = {
                "in": [p.to_dict() for p in spec.in_ports],
                "out": [p.to_dict() for p in spec.out_ports],
            }

        errors = nd.validate()
        if errors:
            return CreateNodeDefinitionResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"errors": errors},
            )

        # Validate inheritance chain (cycle detection + depth)
        if command.parent_definition_id:
            try:
                self.resolver.validate_parent_chain(
                    nd.id, command.parent_definition_id, self.node_definition_repo
                )
            except (CircularInheritanceError, InheritanceDepthExceededError) as e:
                return CreateNodeDefinitionResult(
                    error=ErrorCode.VALIDATION_ERROR.default_message,
                    error_code=ErrorCode.VALIDATION_ERROR.code,
                    error_details={"reason": str(e)},
                )

        self.node_definition_repo.save(nd)
        return _to_result(nd)


def _to_result(nd: NodeDefinition) -> CreateNodeDefinitionResult:
    return CreateNodeDefinitionResult(
        id=nd.id,
        tenant_id=nd.tenant_id,
        name=nd.name,
        description=nd.description,
        parent_definition_id=nd.parent_definition_id,
        base_type=nd.base_type.value if hasattr(nd.base_type, "value") else str(nd.base_type),
        base_worker_id=nd.base_worker_id,
        locked_defaults=nd.locked_defaults,
        input_schema=nd.input_schema,
        output_schema=nd.output_schema,
        input_mapping=nd.input_mapping,
        output_mapping=nd.output_mapping,
        icon=nd.icon,
        color=nd.color,
        tags=nd.tags,
        ports=nd.ports,
        data_mode=nd.data_mode,
        edit_mode=nd.edit_mode,
        kind=nd.kind,
        collection=nd.collection,
        created_at=nd.created_at,
        updated_at=nd.updated_at,
    )
