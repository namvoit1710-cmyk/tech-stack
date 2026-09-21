"""Principal — the resolved caller identity + authorization context (SA RBAC v1, Task 2).

A Principal is the immutable value object every guard checks. It carries the caller's
permission set (derived from their single role) plus the `is_super` / `system` bypass
flags. Guards ask `principal.has(code)`; agent-read visibility asks
`principal.bypass_pool_visibility`.

Two kinds:
  - `user`   — an authenticated caller mirrored in `users` (or a transient default
               user when the mirror can't be written yet, e.g. PM outage).
  - `system` — a token-less internal caller, trusted in Phase 1 via
               `trust_unauthenticated_internal` (B1). Bypasses all checks. In Phase 2
               internal services get a real token and resolve as `user` instead.
"""

from dataclasses import dataclass, field
from typing import Literal

from app.layer1_domain.rbac import AGENT_VIEW_ALL

KIND_USER = "user"
KIND_SYSTEM = "system"

PrincipalKind = Literal["user", "system"]


@dataclass(frozen=True)
class Principal:
    """Immutable authorization context for a single request."""

    kind: PrincipalKind
    user_id: str | None = None
    external_id: str | None = None
    role_code: str | None = None
    is_super: bool = False
    permissions: frozenset[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        """True if the principal is allowed to exercise `permission`.

        `system` and `is_super` principals bypass the permission set entirely.
        """
        if self.kind == KIND_SYSTEM or self.is_super:
            return True
        return permission in self.permissions

    @property
    def bypass_pool_visibility(self) -> bool:
        """True if the principal sees all agents regardless of pool membership."""
        if self.kind == KIND_SYSTEM or self.is_super:
            return True
        return AGENT_VIEW_ALL in self.permissions

    @property
    def is_system(self) -> bool:
        """True for the token-less internal (Phase-1) principal."""
        return self.kind == KIND_SYSTEM

    @classmethod
    def system(cls) -> "Principal":
        """The Phase-1 token-less internal principal (bypasses everything)."""
        return cls(kind=KIND_SYSTEM)

    @classmethod
    def for_user(
        cls,
        *,
        user_id: str | None,
        external_id: str | None,
        role_code: str | None,
        is_super: bool,
        permissions: frozenset[str] | set[str],
    ) -> "Principal":
        """Build a `user` principal from mirror + role data."""
        return cls(
            kind=KIND_USER,
            user_id=user_id,
            external_id=external_id,
            role_code=role_code,
            is_super=is_super,
            permissions=frozenset(permissions),
        )
