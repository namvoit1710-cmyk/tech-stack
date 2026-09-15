"""In-memory implementation of INodeDefinitionRepository."""

from app.layer1_domain.entities.node_definition import NodeDefinition
from app.layer2_application.repositories.node_definition_repository_interface import INodeDefinitionRepository
from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryNodeDefinitionRepository(AsyncRepoMixin, INodeDefinitionRepository):

    def __init__(self) -> None:
        self._store: dict[str, NodeDefinition] = {}

    def save(self, node_definition: NodeDefinition) -> None:
        self._store[node_definition.id] = node_definition

    def get_by_id(self, id: str) -> NodeDefinition | None:
        return self._store.get(id)

    def list_by_tenant(self, tenant_id: str) -> list[NodeDefinition]:
        return [nd for nd in self._store.values() if nd.tenant_id == tenant_id]

    def delete(self, id: str) -> None:
        self._store.pop(id, None)

    def delete_all(self) -> int:
        count = len(self._store)
        self._store.clear()
        return count

    def list_missing_taxonomy(self, tenant_id: str, limit: int) -> list[NodeDefinition]:
        """Rows whose (kind, collection) taxonomy is not yet populated (F/SA-1740).

        Tenant-scoped and bounded (FIX4): only ``tenant_id``'s rows are ever
        candidates, and at most ``limit`` are returned per call so the backfill
        use case can page through a large backlog instead of loading it whole.
        """
        matches = [
            nd for nd in self._store.values()
            if nd.tenant_id == tenant_id and (not nd.kind or not nd.collection)
        ]
        return matches[:limit]
