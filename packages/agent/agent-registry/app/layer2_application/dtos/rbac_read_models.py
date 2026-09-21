"""Read models returned by the RBAC user repository (SA RBAC v1, Task 2).

Small, immutable projections so the application layer never handles ORM rows directly.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MirrorUser:
    """The current mirrored state of a user, joined with their role.

    A single-responsibility projection of `users` ⋈ `roles`: it represents *a user's
    present state*, so it is returned both by queries (list users, resolve a principal's
    row) and as the resulting state of a mutation (assign-role) — a command returning the
    affected resource, not a separate read/command model per caller.
    """

    user_id: str
    external_id: str | None
    email: str | None
    name: str | None
    role_id: str | None
    role_code: str | None
    is_super: bool


@dataclass(frozen=True)
class RoleRef:
    """A lightweight reference to a role (id + code + is_super flag)."""

    role_id: str
    code: str
    is_super: bool


@dataclass(frozen=True)
class RoleDetail:
    """A role with its flags and granted permission codes (for the roles API)."""

    id: str
    code: str
    name: str
    description: str | None
    is_system: bool
    is_super: bool
    permission_codes: tuple[str, ...]


@dataclass(frozen=True)
class PermissionCatalogItem:
    """One entry of the app-defined permission catalog (for the permissions API)."""

    id: str
    code: str
    description: str | None


@dataclass(frozen=True)
class AgentPoolDetail:
    """The current state of an agent pool (active pools only in reads)."""

    id: str
    name: str
    description: str | None
    status: str
    created_by: str | None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class AgentPoolWithLinks:
    """A pool plus the ids of the agents it contains and the users who are members."""

    pool: AgentPoolDetail
    agent_ids: tuple[str, ...]
    member_ids: tuple[str, ...]
