from __future__ import annotations

from typing import Protocol

from app.layer1_domain.entities.catalog import (
    CatalogNode,
    CatalogNodeSchema,
    CatalogWorker,
)


class ICatalogProvider(Protocol):
    """Port that supplies the live workflow palette + per-node config schemas.

    ``nodes`` is the palette summary (id, node_type, ports); ``node_schemas`` fetches the
    full config-field + output-field contract for the given node refs. Both come straight
    from the control-plane node-catalog API so no node metadata is hardcoded in the agent.
    """

    async def nodes(self) -> list[CatalogNode]: ...

    async def node_schemas(self, refs: list[str]) -> list[CatalogNodeSchema]: ...

    async def workers(self) -> list[CatalogWorker]: ...
