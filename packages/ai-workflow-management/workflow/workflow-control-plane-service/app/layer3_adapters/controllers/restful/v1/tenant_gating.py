"""Per-tenant gating helpers for v2 node-catalog rollout (Phase 3).

The v2 endpoints gate on two layers:
1. Global kill-switch ``settings.ENABLE_NODE_CATALOG_V2`` — when false,
   the router is not mounted; requests 404 at the FastAPI layer.
2. Per-tenant allowlist ``settings.NODE_CATALOG_V2_TENANT_ALLOWLIST`` —
   when non-empty, only listed tenants pass through. Others receive 404
   from the controller (so their behavior is identical to "v2 off" at
   the HTTP level, preserving client expectations during rollout).
"""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def _parse_allowlist(raw: str) -> frozenset[str]:
    """Parse the comma-separated allowlist into a frozenset for O(1) lookup.

    Cached so the split happens once per process. The cache key is the
    raw string; if operators flip the env var in-place during a test,
    call :func:`reset_allowlist_cache`.
    """
    return frozenset(
        t.strip()
        for t in (raw or "").split(",")
        if t.strip()
    )


def reset_allowlist_cache() -> None:
    """Clear the parsed-allowlist cache (tests only)."""
    _parse_allowlist.cache_clear()


def tenant_is_gated_out(tenant_id: str, raw_allowlist: str) -> bool:
    """Return True when the tenant should be denied access to v2.

    Empty allowlist → nobody is gated out.
    Non-empty allowlist → tenants not in the list are gated out.
    """
    allowlist = _parse_allowlist(raw_allowlist)
    if not allowlist:
        return False
    return (tenant_id or "").strip() not in allowlist
