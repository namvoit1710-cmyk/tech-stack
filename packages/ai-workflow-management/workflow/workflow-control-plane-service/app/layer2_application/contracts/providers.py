"""NodeProvider protocol — a pluggable source of node catalog entries.

Phase 1 will register three implementations (BuiltinNodeProvider,
WorkerNodeProvider, BusinessNodeProvider) behind a single CatalogService.
Phase 0 ships the protocol shape only.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from app.layer2_application.contracts.node_definition_dto import (
    NodeDefinitionDetail,
    NodeDefinitionSummary,
)


@runtime_checkable
class NodeProvider(Protocol):
    """A source of node catalog entries (builtin, worker, business, ...)."""

    def list_summaries(self, tenant_id: str = "system") -> list[NodeDefinitionSummary]:
        """Return compact entries for the palette list view."""
        ...

    def get_detail(
        self,
        node_id: str,
        spec_version: Optional[str] = None,
    ) -> Optional[NodeDefinitionDetail]:
        """Return the full detail for one node id.

        ``spec_version=None`` means "latest non-deprecated" per Phase-0 rule.
        Returns ``None`` if not found.
        """
        ...
