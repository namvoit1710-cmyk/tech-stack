"""Permission repository port (SA RBAC v1, Task 3)."""

from typing import Protocol

from app.layer2_application.dtos.rbac_read_models import PermissionCatalogItem


class IPermissionRepository(Protocol):
    """Read access to the app-defined permission catalog."""

    def list_permissions(self) -> list[PermissionCatalogItem]:
        """The full seeded permission catalog."""
        ...

    def find_ids_for_codes(self, codes: list[str]) -> dict[str, str]:
        """Map existing permission codes → ids. Unknown codes are omitted.

        The returned dict is UNORDERED (callers must not rely on insertion order); used to
        validate role→permission mappings (the UI cannot invent codes).
        """
        ...
