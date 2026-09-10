"""UC: add a member (user) to a pool (SA RBAC v1, Task 4).

Guarded by `pool.member.add` at the router (so authorization precedes this body). The
target is resolved + mirrored from PM by external_id using the caller's own token (shared
`resolve_mirror_target`, same as assign-role), degrading to an external_id-only upsert if
PM is unset/unreachable.

Note: the PM mirror (`upsert_user`) and the membership insert (`add_member`) run in
separate repository sessions, so they are not one atomic transaction. A failure between
them leaves at most a mirrored user with no membership — a benign orphan (the user would
be mirrored on their next login anyway), never a partial privilege grant.
"""

from app.layer1_domain.exceptions import NotFoundException
from app.layer2_application.dtos.rbac_read_models import MirrorUser
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository
from app.layer2_application.interfaces.fetch_current_user_info_api_client import (
    IFetchCurrentUserInfoApiClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger
from app.layer2_application.interfaces.rbac_user_repository_port import IRbacUserRepository
from app.layer2_application.services.mirror_target import resolve_mirror_target


class AddMemberToPoolUseCase:
    """Add a user (by external_id) to a pool (idempotent)."""

    def __init__(
        self,
        pool_repository: IAgentPoolRepository,
        user_repository: IRbacUserRepository,
        fetch_current_user_info_api_client: IFetchCurrentUserInfoApiClient,
        logger: ILogger | None = None,
    ):
        self.pool_repository = pool_repository
        self.user_repository = user_repository
        self.fetch_current_user_info_api_client = fetch_current_user_info_api_client
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def execute(self, pool_id: str, external_id: str, token: str | None) -> MirrorUser:
        if self.pool_repository.get_pool(pool_id) is None:
            raise NotFoundException("AgentPool", entity_id=pool_id)

        email, name = await resolve_mirror_target(
            self.fetch_current_user_info_api_client, external_id, token, self._logger
        )
        user_id, _ = self.user_repository.upsert_user(external_id, email=email, name=name)
        self.pool_repository.add_member(pool_id, user_id)

        member = self.user_repository.find_by_external_id(external_id)
        if member is None:
            raise NotFoundException("User", entity_id=user_id)
        return member
