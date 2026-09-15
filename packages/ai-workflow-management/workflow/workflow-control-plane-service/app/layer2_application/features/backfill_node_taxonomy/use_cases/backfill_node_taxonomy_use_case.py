"""SA-1740 (F): proactively persist (kind, collection) onto legacy rows."""

from dataclasses import dataclass
from typing import Any

from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer2_application.repositories.node_definition_repository_interface import (
    INodeDefinitionRepository,
)
from app.layer1_domain.value_objects.node_kind import MINE_COLLECTION, default_kind

# Bounds each page pulled from the repo so a tenant with a huge legacy backlog
# never gets fully materialised in memory in one shot (FIX4).
_DEFAULT_PAGE_SIZE = 200


@dataclass
class BackfillNodeTaxonomyResult:
    backfilled: int = 0


class BackfillNodeTaxonomyUseCase:
    """Persist (kind, collection) for rows that still lack them, for ONE tenant.

    Read-only over meaning: it fills a field only when that field is blank, so a
    user-set kind/collection is never overwritten (AC4 no-data-loss). Idempotent —
    a second run finds no rows. ``kind`` takes the deterministic pure default;
    parent inheritance is a create-time nicety (F1), not applied to legacy rows.

    FIX4 (SECURITY): this used to run unscoped across every tenant with the
    whole legacy table materialised in memory in one query. It is now REQUIRED
    to name a ``tenant_id`` and pages through ``list_missing_taxonomy`` in
    bounded chunks until a page comes back short — so a legacy backlog is
    processed incrementally instead of loaded whole, and one tenant's run can
    never touch another tenant's rows.
    """

    def __init__(self, logger: ILogger, node_definition_repo: INodeDefinitionRepository, **kwargs: Any) -> None:
        self.logger = logger
        self.node_definition_repo = node_definition_repo

    async def execute(self, tenant_id: str, limit: int = _DEFAULT_PAGE_SIZE) -> BackfillNodeTaxonomyResult:
        count = 0
        while True:
            rows = self.node_definition_repo.list_missing_taxonomy(tenant_id, limit)
            if not rows:
                break
            for nd in rows:
                if not nd.kind:
                    nd.kind = default_kind(nd.base_type, nd.locked_defaults)
                if not nd.collection:
                    nd.collection = MINE_COLLECTION
                self.node_definition_repo.save(nd)
                count += 1
            if len(rows) < limit:
                break
        self.logger.info("Backfilled node taxonomy", count=count, tenant_id=tenant_id)
        return BackfillNodeTaxonomyResult(backfilled=count)
