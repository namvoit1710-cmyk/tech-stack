"""Port for the per-tenant node-catalog cache.

Layer 2 depends on this Protocol; the concrete TTL cache lives in
layer4_frameworks/caching and satisfies it structurally.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol


class INodeCatalogCache(Protocol):
    def get(self, tenant_id: str) -> Any | None: ...

    def put(self, tenant_id: str, result: Any, etag: str) -> None: ...

    def invalidate(self, tenant_id: Optional[str] = None) -> None: ...
