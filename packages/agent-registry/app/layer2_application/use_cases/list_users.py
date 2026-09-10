"""UC: list the mirrored users + their role (SA RBAC v1, Task 3)."""

from app.layer2_application.dtos.rbac_read_models import MirrorUser
from app.layer2_application.interfaces.rbac_user_repository_port import IRbacUserRepository


class ListUsersUseCase:
    """Return the RBAC user mirror joined with each user's role. Guarded by `user.read`."""

    def __init__(self, repository: IRbacUserRepository):
        self.repository = repository

    def execute(self) -> list[MirrorUser]:
        return self.repository.list_users()
