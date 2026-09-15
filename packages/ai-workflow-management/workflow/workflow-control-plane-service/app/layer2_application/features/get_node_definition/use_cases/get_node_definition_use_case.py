"""Retrieve a single node definition by ID."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer2_application.errors import ErrorCode
from app.layer1_domain.entities.node_definition import NodeDefinition


@dataclass
class GetNodeDefinitionCommand:
    node_definition_id: str = ""


@dataclass
class GetNodeDefinitionResult:
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


class GetNodeDefinitionUseCase:
    def __init__(self, logger: ILogger, node_definition_repo: INodeDefinitionRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo

    async def execute(self, command: GetNodeDefinitionCommand) -> GetNodeDefinitionResult:
        self.logger.info("Getting node definition", id=command.node_definition_id)
        nd = self.node_definition_repo.get_by_id(command.node_definition_id)
        if not nd:
            return GetNodeDefinitionResult(
                error=ErrorCode.NODE_DEFINITION_NOT_FOUND.default_message,
                error_code=ErrorCode.NODE_DEFINITION_NOT_FOUND.code,
                error_details={"node_definition_id": command.node_definition_id},
            )
        return _to_result(nd)


def _to_result(nd: NodeDefinition) -> GetNodeDefinitionResult:
    return GetNodeDefinitionResult(
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
