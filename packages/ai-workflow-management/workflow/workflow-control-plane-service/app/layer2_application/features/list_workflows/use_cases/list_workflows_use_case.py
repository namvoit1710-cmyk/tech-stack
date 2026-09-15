"""List workflows with optional OData-style pagination and filtering."""

from dataclasses import dataclass, field
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.workflow_repository_interface import (
    IWorkflowRepository,
    ListWorkflowsFilter,
)


@dataclass
class ListWorkflowsCommand:
    status: str | None = None
    tenant_id: str | None = None
    main_flow: bool | None = None
    skip: int = 0
    top: int = 20
    orderby: str = ""
    eq: dict[str, Any] = field(default_factory=dict)
    ne: dict[str, Any] = field(default_factory=dict)
    gt: dict[str, Any] = field(default_factory=dict)
    ge: dict[str, Any] = field(default_factory=dict)
    lt: dict[str, Any] = field(default_factory=dict)
    le: dict[str, Any] = field(default_factory=dict)
    contains: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    version: str | None = None
    # Opt-in: resolve each workflow's current row to its PUBLISHED version when one
    # exists (else latest-edited). Default False keeps the editor's latest-edited view.
    prefer_published: bool = False

@dataclass
class ListWorkflowsResult:
    items: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    # Keep backward compat alias
    workflows: list[dict[str, Any]] = field(default_factory=list)


class ListWorkflowsUseCase:
    def __init__(self, logger: ILogger, workflow_repo: IWorkflowRepository,
                 keycloak_admin_client: Any = None, **kwargs: Any) -> None:
        self.logger = logger
        self.workflow_repo = workflow_repo
        # SA-2570: resolve owner ids → display names server-side (from Keycloak,
        # cached) so the FE list shows names without pre-fetching /users.
        self.keycloak_admin_client = keycloak_admin_client

    async def execute(self, command: ListWorkflowsCommand | None) -> ListWorkflowsResult:
        self.logger.info("Listing workflows")

        has_filters = bool(
            command and (
                command.skip or command.top != 20 or command.status or command.orderby
                or command.eq or command.ne or command.gt or command.ge
                or command.lt or command.le or command.contains or command.id or command.version
                or command.prefer_published
            )
        )
        if has_filters:
            # Fold legacy ``status`` arg into the eq dict so callers passing
            # ``ListWorkflowsCommand(status="DRAFT")`` keep working.
            eq = dict(command.eq)
            if command.status and "status" not in eq:
                eq["status"] = command.status
            filters = ListWorkflowsFilter(
                status=command.status,
                tenant_id=command.tenant_id,
                main_flow=command.main_flow,
                skip=command.skip,
                top=command.top,
                orderby=command.orderby,
                eq=eq,
                ne=command.ne,
                gt=command.gt,
                ge=command.ge,
                lt=command.lt,
                le=command.le,
                contains=command.contains,
                id=command.id,
                version=command.version,
                prefer_published=command.prefer_published,
            )
            page = self.workflow_repo.list_paged(filters)
            items = []
            for w in page.items:
                items.append(self._to_dict(w))
            await self._attach_owner_names(items)
            return ListWorkflowsResult(items=items, total=page.total, workflows=items)
        else:
            # Backward compat: return all
            workflows = await self.workflow_repo.alist()
            items = [self._to_dict(w) for w in workflows]
            await self._attach_owner_names(items)
            return ListWorkflowsResult(items=items, total=len(items), workflows=items)

    async def _attach_owner_names(self, items: list[dict[str, Any]]) -> None:
        """Resolve each item's owner id → display name via Keycloak (cached)."""
        if not self.keycloak_admin_client:
            return
        try:
            names = await self.keycloak_admin_client.name_map()
        except Exception:  # noqa: BLE001 — names are best-effort
            names = {}
        for it in items:
            owner = it.get("owner")
            it["owner_name"] = names.get(owner) if owner else None

    @staticmethod
    def _to_dict(w: Any) -> dict[str, Any]:
        return {
            "id": w.id, "name": w.name, "version": str(w.version),
            "description": w.description, "status": w.status.value if hasattr(w.status, "value") else str(w.status),
            "created_at": w.created_at, "test_run_id": w.test_run_id,
            "routing_path": w.routing_path, "main_flow": w.main_flow,
            # SA-2570: attribution for the list view (owner column / Created by).
            "created_by": getattr(w, "created_by", None) or None,
            "updated_by": getattr(w, "updated_by", None) or None,
            "owner": getattr(w, "owner", None) or None,
        }
