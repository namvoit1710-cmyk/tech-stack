"""Update an existing node definition (PATCH semantics)."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer1_domain.value_objects.worker_ref import WorkerRef
from app.layer2_application.definition.node_definition_resolver import NodeDefinitionResolver
from app.layer1_domain.exceptions import CircularInheritanceError, InheritanceDepthExceededError
from app.layer1_domain.node_spec import get_spec

# Sentinel to distinguish "not provided" from "explicitly set to None"
_UNSET = object()


@dataclass
class UpdateNodeDefinitionCommand:
    node_definition_id: str = ""
    name: str | None = None
    description: str | None = None
    parent_definition_id: Any = _UNSET  # use _UNSET as default so None means "clear parent"
    base_type: str | None = None
    base_worker_id: str | None = None
    locked_defaults: dict[str, Any] | None = None
    input_schema: list[dict[str, Any]] | None = None
    output_schema: list[dict[str, Any]] | None = None
    input_mapping: dict[str, Any] | None = None
    output_mapping: dict[str, Any] | None = None
    icon: str | None = None
    color: str | None = None
    tags: list[str] | None = None
    ports: dict[str, Any] | None = None
    data_mode: str | None = None
    edit_mode: str | None = None
    kind: str | None = None
    collection: str | None = None


@dataclass
class UpdateNodeDefinitionResult:
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


class UpdateNodeDefinitionUseCase:
    def __init__(self, logger: ILogger, node_definition_repo: INodeDefinitionRepository,
                 node_definition_resolver: NodeDefinitionResolver | None = None, **kwargs: Any) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo
        self.resolver = node_definition_resolver or NodeDefinitionResolver()

    async def execute(self, command: UpdateNodeDefinitionCommand) -> UpdateNodeDefinitionResult:
        self.logger.info("Updating node definition", id=command.node_definition_id)
        nd = self.node_definition_repo.get_by_id(command.node_definition_id)
        if not nd:
            return UpdateNodeDefinitionResult(
                error=ErrorCode.NODE_DEFINITION_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_DEFINITION_NOT_FOUND.code,
                error_details={"node_definition_id": command.node_definition_id},
            )

        if command.name is not None:
            nd.name = command.name
        if command.description is not None:
            nd.description = command.description
        if command.parent_definition_id is not _UNSET:
            # Validate parent reference if being set
            new_parent_id = command.parent_definition_id
            if new_parent_id:
                parent = self.node_definition_repo.get_by_id(new_parent_id)
                if not parent:
                    return UpdateNodeDefinitionResult(
                        error=ErrorCode.PARENT_DEFINITION_NOT_FOUND.default_message,
                        error_code=ErrorCode.PARENT_DEFINITION_NOT_FOUND.code,
                    )
                if parent.tenant_id != nd.tenant_id:
                    return UpdateNodeDefinitionResult(
                        error=ErrorCode.TENANT_MISMATCH.default_message,
                        error_code=ErrorCode.TENANT_MISMATCH.code,
                    )
                try:
                    self.resolver.validate_parent_chain(
                        nd.id, new_parent_id, self.node_definition_repo
                    )
                except (CircularInheritanceError, InheritanceDepthExceededError) as e:
                    return UpdateNodeDefinitionResult(
                        error=ErrorCode.VALIDATION_ERROR.default_message,
                        error_code=ErrorCode.VALIDATION_ERROR.code,
                        error_details={"reason": str(e)},
                    )
            nd.parent_definition_id = new_parent_id
        if command.base_type is not None:
            nd.base_type = command.base_type
        if command.base_worker_id is not None:
            # Normalize to the bare worker_type (PATCH mutates the field directly,
            # bypassing NodeDefinition.__post_init__). Mirrors the entity invariant.
            ref = WorkerRef.parse(command.base_worker_id)
            nd.base_worker_id = ref.worker_type if ref is not None else command.base_worker_id
        if command.locked_defaults is not None:
            nd.locked_defaults = command.locked_defaults
        if command.input_schema is not None:
            nd.input_schema = command.input_schema
        if command.output_schema is not None:
            nd.output_schema = command.output_schema
        if command.input_mapping is not None:
            nd.input_mapping = command.input_mapping
        if command.output_mapping is not None:
            nd.output_mapping = command.output_mapping
        if command.icon is not None:
            nd.icon = command.icon
        if command.color is not None:
            nd.color = command.color
        if command.tags is not None:
            nd.tags = command.tags
        if command.ports is not None:
            nd.ports = command.ports
        if command.data_mode is not None:
            nd.data_mode = command.data_mode
        if command.edit_mode is not None:
            nd.edit_mode = command.edit_mode
        # Defence in depth: "" is NEVER a valid kind (it is not in VALID_NODE_KINDS), so a blank
        # can only mean "the client had no value" — never "set it to blank". Treat it as no-change.
        # The root cause (GET not returning `kind`, so the builder form posted "") is fixed above,
        # but this stops the same class of client bug from ever destroying an author's choice again.
        if command.kind:
            nd.kind = command.kind
        # FIX3: symmetric with `kind` above. A blank `collection` used to be treated as
        # "reset to My Nodes" — but the exact same stale client that posts `kind=""` also
        # posts `collection=""`, so that "reset" silently wiped a user's real collection
        # (e.g. "Finance") on every no-op save. A blank must never destroy: a user who
        # wants the default collection can say so explicitly ("My Nodes" costs nothing).
        if command.collection:
            nd.collection = command.collection

        # Auto-populate ports from the node spec if still empty
        if not nd.ports or (not nd.ports.get("in") and not nd.ports.get("out")):
            spec = get_spec(nd.base_type)
            nd.ports = {
                "in": [p.to_dict() for p in spec.in_ports],
                "out": [p.to_dict() for p in spec.out_ports],
            }

        errors = nd.validate()
        if errors:
            return UpdateNodeDefinitionResult(
                error=ErrorCode.VALIDATION_ERROR.default_message,
                error_code=ErrorCode.VALIDATION_ERROR.code,
                error_details={"errors": errors},
            )

        nd.updated_at = datetime.now(timezone.utc).isoformat()
        self.node_definition_repo.save(nd)
        return _to_result(nd)


def _to_result(nd: NodeDefinition) -> UpdateNodeDefinitionResult:
    return UpdateNodeDefinitionResult(
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
