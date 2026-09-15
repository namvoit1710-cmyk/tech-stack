"""List node definitions filtered by tenant."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer1_domain.entities.node_definition import NodeDefinition


@dataclass
class ListNodeDefinitionsCommand:
    tenant_id: str = "system"
    skip: int = 0
    top: int = 20
    orderby: str = ""


@dataclass
class ListNodeDefinitionsResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0


class ListNodeDefinitionsUseCase:
    def __init__(self, logger: ILogger, node_definition_repo: INodeDefinitionRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo

    async def execute(self, command: ListNodeDefinitionsCommand) -> ListNodeDefinitionsResult:
        self.logger.info("Listing node definitions", tenant_id=command.tenant_id)
        definitions = self.node_definition_repo.list_by_tenant(command.tenant_id)
        items = [_to_dict(nd) for nd in definitions]

        # Sort if orderby is provided (e.g. "name asc", "created_at desc")
        if command.orderby:
            parts = command.orderby.strip().split()
            sort_field = parts[0]
            descending = len(parts) > 1 and parts[1].lower() == "desc"
            items.sort(key=lambda x: x.get(sort_field, ""), reverse=descending)

        total = len(items)
        items = items[command.skip : command.skip + command.top]
        return ListNodeDefinitionsResult(items=items, total=total)


def _to_dict(nd: NodeDefinition) -> dict[str, Any]:
    return {
        "id": nd.id,
        "tenant_id": nd.tenant_id,
        "name": nd.name,
        "description": nd.description,
        "parent_definition_id": nd.parent_definition_id,
        "base_type": nd.base_type.value if hasattr(nd.base_type, "value") else str(nd.base_type),
        "base_worker_id": nd.base_worker_id,
        "locked_defaults": nd.locked_defaults,
        "input_schema": nd.input_schema,
        "output_schema": nd.output_schema,
        "input_mapping": nd.input_mapping,
        "output_mapping": nd.output_mapping,
        "icon": nd.icon,
        "color": nd.color,
        "tags": nd.tags,
        "ports": nd.ports,
        "data_mode": nd.data_mode,
        "edit_mode": nd.edit_mode,
        "kind": nd.kind,
        "collection": nd.collection,
        "created_at": nd.created_at,
        "updated_at": nd.updated_at,
    }
