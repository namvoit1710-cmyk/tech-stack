"""UC: assign a role to a user with R1–R3 safeguards (SA RBAC v1, Task 3).

The required permission depends on the TRANSITION (role removed → role added):
each of the admin / super_admin / custom tiers contributes its `(un)assign_*_role`
permission; the base `user` role is neutral. This encodes R1/R2 (only super_admin holds
the `*_super_admin_role` perms) and makes custom-role assignment super_admin-only. R3
(cannot remove the last super_admin) stays a runtime 409.

The target is mirrored from PM by external_id using the caller's own token; if PM is
unset/unreachable the target is still upserted by external_id (email filled on their
next login — email is cosmetic, authz keys on external_id + role).
"""

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
)
from app.layer1_domain.rbac import ADMIN_ROLE, USER_ROLE
from app.layer2_application.dtos.rbac_read_models import MirrorUser, RoleDetail
from app.layer2_application.interfaces.fetch_current_user_info_api_client import (
    IFetchCurrentUserInfoApiClient,
)
from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger
from app.layer2_application.interfaces.rbac_user_repository_port import IRbacUserRepository
from app.layer2_application.interfaces.role_repository_port import IRoleRepository
from app.layer2_application.services.mirror_target import resolve_mirror_target


class AssignRoleUseCase:
    """Assign `new_role_id` to the user identified by `external_id`."""

    def __init__(
        self,
        user_repository: IRbacUserRepository,
        role_repository: IRoleRepository,
        fetch_current_user_info_api_client: IFetchCurrentUserInfoApiClient,
        logger: ILogger | None = None,
    ):
        self.user_repository = user_repository
        self.role_repository = role_repository
        self.fetch_current_user_info_api_client = fetch_current_user_info_api_client
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def execute(
        self, caller: Principal, external_id: str, new_role_id: str, token: str | None
    ) -> MirrorUser:
        new_role = self.role_repository.get_role(new_role_id)
        if new_role is None:
            raise NotFoundException("Role", entity_id=new_role_id)

        # Determine the target's CURRENT role with a READ-ONLY lookup — authorization
        # (and R3) must be decided BEFORE any side effect (PM fetch / mirror write), so a
        # caller who fails the permission check never triggers a PM probe or an INSERT.
        existing = self.user_repository.find_by_external_id(external_id)
        if existing is not None and existing.role_code:
            current_role = self.role_repository.get_role_by_code(existing.role_code)
        else:
            # An unseen target is mirrored with the default `user` role on assign, so the
            # transition is (user → new_role); `user` is the neutral tier.
            current_role = self.role_repository.get_role_by_code(USER_ROLE)

        # Required permission(s) = tier of removed role + tier of added role.
        # Authorization is decided BEFORE the no-op short-circuit so this privileged
        # mutation endpoint can never be used to read a target's record without the right
        # to touch that role tier (L-1).
        required = {
            perm
            for perm in (self._tier_perm(current_role, "unassign"), self._tier_perm(new_role, "assign"))
            if perm is not None
        }
        missing = sorted(p for p in required if not caller.has(p))
        if missing:
            raise ForbiddenException(
                f"Missing permission(s) for this role change: {', '.join(missing)}"
            )

        # No-op: target already holds the requested role → nothing to change. Return a
        # MINIMAL ack (email/name redacted) so this endpoint can't double as an
        # unauthenticated read of a user's PII (L-1).
        if existing is not None and current_role is not None and current_role.id == new_role.id:
            return MirrorUser(
                user_id=existing.user_id,
                external_id=existing.external_id,
                email=None,
                name=None,
                role_id=existing.role_id,
                role_code=existing.role_code,
                is_super=existing.is_super,
            )

        # R3: cannot remove the last super_admin (read-only count, still pre-write).
        # count_users_with_super_role INNER-joins users→roles; sound because a super user
        # always has a non-NULL role_id (the mirror requires a role). Runtime rule → 409;
        # the read-then-write is a documented low-severity TOCTOU (not a DB constraint).
        removing_super = current_role is not None and current_role.is_super and not new_role.is_super
        if removing_super and self.user_repository.count_users_with_super_role() <= 1:
            raise ConflictException(
                "Cannot remove the last super_admin — at least one must remain"
            )

        # Authorized → now mirror the target (PM, caller's token) and assign the role.
        email, name = await resolve_mirror_target(
            self.fetch_current_user_info_api_client, external_id, token, self._logger
        )
        target_user_id, _ = self.user_repository.upsert_user(external_id, email=email, name=name)
        self.user_repository.set_role(target_user_id, new_role.id)
        return self._refresh(external_id, target_user_id)

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _tier_perm(role: RoleDetail | None, verb: str) -> str | None:
        """The `user.role.{verb}_{tier}_role` permission for touching `role`.

        `super_admin` → super_admin tier; built-in `admin` → admin tier; built-in `user`
        → neutral (None); any other role → custom tier. Today the seed has exactly these
        three built-ins and `create_role` forces is_system=False, so "any other role" is
        always a custom role. Should a 4th built-in ever be seeded, it would fall to the
        custom tier — i.e. become super_admin-only to assign — which is fail-safe (more
        restrictive), not an escalation.
        """
        if role is None:
            return None
        if role.is_super:
            return f"user.role.{verb}_super_admin_role"
        if role.is_system and role.code == ADMIN_ROLE:
            return f"user.role.{verb}_admin_role"
        if role.is_system and role.code == USER_ROLE:
            return None
        return f"user.role.{verb}_custom_role"

    def _refresh(self, external_id: str, fallback_user_id: str) -> MirrorUser:
        record = self.user_repository.find_by_external_id(external_id)
        if record is not None:
            return record
        raise NotFoundException("User", entity_id=fallback_user_id)
