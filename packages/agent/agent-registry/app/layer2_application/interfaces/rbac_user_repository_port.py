"""RBAC user repository port (SA RBAC v1, Task 2).

The RBAC mirror keyed on `external_id`. Since T5, this is the ONLY user repository — the
legacy email-keyed `IUserRepository`/`HANAUserRepository` (used by the removed
/mine //assign //assignable paths) was folded away in the T5 contract migration.
"""

from typing import Protocol

from app.layer2_application.dtos.rbac_read_models import MirrorUser, RoleRef


class IRbacUserRepository(Protocol):
    """Persistence contract for the RBAC user mirror, keyed on external_id."""

    def find_by_external_id(self, external_id: str) -> MirrorUser | None:
        """Return the mirrored user (joined with their role) or None if unknown."""
        ...

    def upsert_user(
        self, external_id: str, email: str | None = None, name: str | None = None
    ) -> tuple[str, str | None]:
        """Insert the user (default `user` role) if new, else refresh email/name only.

        Never overwrites the role on update. Concurrency-safe on `external_id`.

        Returns:
            (user_id, role_code)
        """
        ...

    def set_role(self, user_id: str, role_id: str) -> None:
        """Assign a role to an existing user (used by the assign-role use case, T3)."""
        ...

    def count_users_with_super_role(self) -> int:
        """Count users whose role has is_super=True (anti-lockout R3 check)."""
        ...

    def list_users(self) -> list[MirrorUser]:
        """List all mirrored users joined with their role (GET /users, T3)."""
        ...

    def get_permissions_for_role(self, role_id: str) -> set[str]:
        """Return the set of permission codes granted to a role."""
        ...

    def find_role_by_code(self, code: str) -> RoleRef | None:
        """Return a role reference by its stable code, or None if absent."""
        ...
