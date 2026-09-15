from __future__ import annotations

import operator
from collections import deque

from app.layer1_domain.entities.workflow import Workflow
from app.layer1_domain.value_objects.workflow_status import WorkflowStatus
from app.layer1_domain.value_objects.node_type import NodeType
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
    ListWorkflowsFilter,
    ListWorkflowsPage,
    TriggerRoute,
)
from typing import Any

_FILTERABLE = frozenset({
    "id", "name", "version", "description", "status", "routing_path",
    "purpose", "schema_version", "test_run_id", "action", "instruction",
    "main_flow", "created_at", "updated_at",
})
_CONTAINS_FILTERABLE = frozenset({
    "id", "name", "version", "description", "status", "routing_path",
    "purpose", "schema_version", "test_run_id", "action", "instruction",
})
_OP_FNS = {
    "eq": operator.eq, "ne": operator.ne,
    "gt": operator.gt, "ge": operator.ge,
    "lt": operator.lt, "le": operator.le,
}

from app.layer4_frameworks.persistence.memory.async_repo_mixin import AsyncRepoMixin


class MemoryWorkflowRepository(AsyncRepoMixin, IWorkflowRepository):
    def __init__(self) -> None:
        self._store: dict[str, Workflow] = {}
        self._relations: dict[str, list[tuple[str, str]]] = {}  # parent_id -> [(node_id, child_id)]

    def save(self, workflow: Workflow) -> None:
        self._store[workflow.id] = workflow
        self._sync_relations(workflow)
    
    async def asave(self, workflow: Workflow) -> None:
        self.save(workflow)

    def _sync_relations(self, workflow: Workflow) -> None:
        relations = [
            (node.id, node.config.get("workflow_id"))
            for node in workflow.nodes
            if node.node_type == NodeType.WORKFLOW and node.config.get("workflow_id")
        ]
        if relations:
            self._relations[workflow.id] = relations
        else:
            self._relations.pop(workflow.id, None)

    def get_by_id(self, id: str) -> Workflow | None:
        return self._store.get(id)
    
    async def aget_by_id(self, id: str) -> Workflow | None:
        return self.get_by_id(id)

    # NOTE: IWorkflowRepository is a Protocol whose ``a*`` methods have
    # ``...`` bodies. Inheriting from it makes those concrete (returning
    # None), which SHADOWS ``AsyncRepoMixin.__getattr__`` — so every async
    # method the engine calls must be overridden explicitly here, mirroring
    # HanaWorkflowRepository. Missing one silently returns None (e.g. StartRun
    # → "Workflow not found").
    async def aget_by_id_version(self, id: str, workflow_version: str) -> Workflow | None:
        return self.get_by_id_version(id, workflow_version)

    async def aget_by_latest_publish_version(self, id: str) -> Workflow | None:
        return self.get_by_latest_publish_version(id)

    def get_by_routing_path(self, routing_path: str) -> Workflow | None:
        for w in self._store.values():
            if w.routing_path == routing_path:
                return w
        return None

    def list(self) -> list[Workflow]:
        return list(self._store.values())

    def delete(self, id: str) -> None:
        self._store.pop(id, None)
        self.delete_workflow_relations(id)

    def delete_all(self) -> int:
        count = len(self._store)
        self._store.clear()
        self._relations.clear()
        return count

    def list_paged(self, filters: ListWorkflowsFilter) -> ListWorkflowsPage:
        items = list(self._store.values())
        for op_name in ("eq", "ne", "gt", "ge", "lt", "le"):
            for attr, val in (getattr(filters, op_name) or {}).items():
                if attr not in _FILTERABLE:
                    continue
                items = [w for w in items if self._matches(w, attr, op_name, val)]
        for attr, search in (filters.contains or {}).items():
            if attr not in _CONTAINS_FILTERABLE:
                continue
            search_lower = search.lower()
            items = [
                w for w in items
                if search_lower in self._attr_to_str(w, attr).lower()
            ]
        total = len(items)
        if filters.orderby:
            field = filters.orderby.replace(" desc", "").replace(" asc", "").strip()
            reverse = "desc" in filters.orderby
            items.sort(key=lambda w: getattr(w, field, ""), reverse=reverse)
        items = items[filters.skip: filters.skip + filters.top]
        return ListWorkflowsPage(items=items, total=total)

    @classmethod
    def _matches(cls, w: Any, attr: str, op_name: str, val: str) -> bool:
        op_fn = _OP_FNS[op_name]
        if attr == "main_flow":
            target = str(val).lower() in ("true", "1")
            return op_fn(bool(getattr(w, "main_flow", False)), target)
        return op_fn(cls._attr_to_str(w, attr), str(val))

    @staticmethod
    def _attr_to_str(obj: Any, field: str) -> str:
        val = getattr(obj, field, "")
        if hasattr(val, "value"):
            val = val.value
        return str(val or "")

    def list_main_flows(self) -> list[Workflow]:
        return [w for w in self._store.values() if w.main_flow]

    def list_trigger_routes(
        self, trigger_types: tuple[str, ...] = (),
    ) -> list[TriggerRoute]:
        """PUBLISHED main flows only — HANA parity.

        ``_store`` is keyed by ENTITY id, so a workflow's version rows live
        under distinct keys sharing ``workflow_id``. That makes the same stale
        row possible here, which is why this applies the same status gate
        rather than trusting the store to hold one row per workflow.
        """
        from app.layer2_application.repositories.workflow_repository_interface import (
            build_trigger_routes,
        )

        routes: list[TriggerRoute] = []
        for w in self._store.values():
            if not w.main_flow or w.status != WorkflowStatus.PUBLISHED:
                continue
            routes.extend(build_trigger_routes(
                w.id, w.version, w.nodes, trigger_types,
            ))
        return routes

    def get_all_child_workflows(self, workflow_id: str) -> list[dict]:
        """BFS traversal to find all descendant workflows."""
        visited: set[str] = {workflow_id}
        queue: deque[str] = deque([workflow_id])
        result: list[dict] = []
        while queue:
            current_id = queue.popleft()
            wf = self._store.get(current_id)
            if not wf:
                continue
            nodes_data = [
                {
                    "id": n.id, "name": n.name,
                    "node_type": n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type),
                    "config": n.config,
                }
                for n in wf.nodes
            ]
            result.append({"id": wf.id, "name": wf.name, "nodes": nodes_data})
            for _node_id, child_id in self._relations.get(current_id, []):
                if child_id not in visited:
                    visited.add(child_id)
                    queue.append(child_id)
        return result

    def save_workflow_relations(self, workflow_id: str, relations: list[tuple[str, str]]) -> None:
        self._relations[workflow_id] = list(relations)

    def delete_workflow_relations(self, workflow_id: str) -> None:
        self._relations.pop(workflow_id, None)

    def get_child_workflow_ids(self, workflow_id: str) -> list[dict]:
        return [
            {
                "parent_workflow_id": workflow_id,
                "child_workflow_id": child_id,
                "node_id": node_id,
            }
            for node_id, child_id in self._relations.get(workflow_id, [])
        ]

    def get_workflows_by_ids(self, ids: list[str]) -> list[Workflow]:
        return [self._store[wid] for wid in ids if wid in self._store]

    def get_latest_version_by_workflow_id(self, workflow_id: str) -> str | None:
        """Get the latest version string for a given workflow_id.
        
        Determines the latest version by finding the workflow with the most recent updated_at timestamp.
        """
        workflows = [
            w for w in self._store.values()
            if w.workflow_id == workflow_id and w.version is not None
        ]
        if not workflows:
            return None
        
        # Find the workflow with the most recent updated_at timestamp
        latest_workflow = max(workflows, key=lambda w: w.updated_at)
        return latest_workflow.version

    def get_by_id_version(self, id: str, version: str) -> Workflow | None:
        for w in self._store.values():
            if w.workflow_id == id and w.version == version:
                return w
        return None

    def get_list_version_by_status(
        self, id: str, version: str, status: str,
    ) -> list[Workflow]:
        """Other versions of ``id`` in ``status`` (mirrors the HANA query:
        same ID, VERSION != version, STATUS = status). Was missing here —
        the Protocol default silently returned None and publish crashed
        iterating it."""
        status_value = status.value if hasattr(status, "value") else str(status)
        return [
            w for w in self._store.values()
            if (w.id == id or w.workflow_id == id)
            and w.version != version
            and (w.status.value if hasattr(w.status, "value") else str(w.status))
            == status_value
        ]

    async def aget_list_version_by_status(
        self, id: str, version: str, status: str,
    ) -> list[Workflow]:
        return self.get_list_version_by_status(id, version, status)

    def get_by_latest_publish_version(self, id: str) -> Workflow | None:
        """Latest PUBLISHED version if one exists, else latest DRAFT.

        Mirrors ``HanaWorkflowRepository.get_by_latest_publish_version``
        (PUBLISHED preferred over DRAFT, newest wins; other statuses are
        never returned). The previous strict-PUBLISHED, workflow_id-only
        filter diverged from the production contract and made every
        DRAFT fixture fail ``StartRunUseCase`` with "Workflow not found".

        Rows match on the logical id — HANA's ID column is the logical
        workflow id shared across version rows, which maps to either
        ``w.id`` or ``w.workflow_id`` here (fixtures commonly construct
        ``Workflow(id=...)`` and let ``workflow_id`` default to a uuid).
        """
        candidates = [
            w for w in self._store.values()
            if (w.id == id or w.workflow_id == id)
            and w.status in (WorkflowStatus.PUBLISHED, WorkflowStatus.DRAFT)
        ]
        if not candidates:
            return None

        published = [w for w in candidates if w.status == WorkflowStatus.PUBLISHED]
        pool = published or candidates

        def _newest(w: Workflow) -> tuple:
            try:
                version = int(w.version)
            except (TypeError, ValueError):
                version = 0
            # Newest updated_at wins (HANA's ORDER BY), version tie-break.
            return (w.updated_at or "", version)

        return max(pool, key=_newest)