"""Resolve the request Principal (SA RBAC v1, Task 2).

Turns a bearer token (or its absence) into a Principal carrying the caller's permission
set. Design constraints:
  - Read ONLY the id (external_id) from the JWT; email/name come from Profile Management.
  - Keep PM off the hot path: for a KNOWN user (mirror row present) never call PM
    (missing-only staleness — finding H1).
  - Degrade gracefully: if the row is absent AND PM is unreachable, return a transient
    default-`user` principal WITHOUT persisting (no 500 on reads). The mirror row is
    written on a later request once PM recovers.
  - Phase-1 fail-open: a token-less call resolves to `system` when
    `trust_unauthenticated_internal` is on, else 401 (B1).

Trust boundary (IMPORTANT): the JWT signature is verified UPSTREAM at the gateway; this
service reads only the id claim and never re-verifies the signature. That makes the
whole authorization model contingent on the gateway sitting in front of every route and
stripping client-supplied `Authorization` on token-less internal paths. The reader adds
a cheap local `exp` check as defense-in-depth, but this precondition is a hard
deployment invariant, not something the code can fully enforce.
"""

from app.layer1_domain.entities.principal import Principal
from app.layer1_domain.exceptions import InvalidDataException, UnauthenticatedException
from app.layer1_domain.rbac import DEFAULT_USER_PERMISSIONS, USER_ROLE
from app.layer2_application.interfaces.fetch_current_user_info_api_client import (
    IFetchCurrentUserInfoApiClient,
)
from app.layer2_application.interfaces.identity_token_reader_port import IIdentityTokenReader
from app.layer2_application.interfaces.logger_interface import ILogger, NullLogger
from app.layer2_application.interfaces.rbac_user_repository_port import IRbacUserRepository


class ResolvePrincipalUseCase:
    """Resolve a Principal from the request's bearer token."""

    def __init__(
        self,
        user_repository: IRbacUserRepository,
        identity_token_reader: IIdentityTokenReader,
        fetch_current_user_info_api_client: IFetchCurrentUserInfoApiClient,
        trust_unauthenticated_internal: bool = True,
        logger: ILogger | None = None,
    ):
        self.user_repository = user_repository
        self.identity_token_reader = identity_token_reader
        self.fetch_current_user_info_api_client = fetch_current_user_info_api_client
        self.trust_unauthenticated_internal = trust_unauthenticated_internal
        self._logger: ILogger = logger if logger is not None else NullLogger()

    async def execute(self, token: str | None) -> Principal:
        """Resolve the caller into a Principal.

        Args:
            token: the raw bearer token (no "Bearer " prefix), or None.

        Raises:
            UnauthenticatedException: token-less with the fail-open flag off, or a
                token that carries no (valid) user id.
        """
        # (3)/(4) No token → system (flag on) or 401 (flag off).
        if not token:
            if self.trust_unauthenticated_internal:
                # A `system` principal bypasses ALL permission + pool-visibility checks.
                # Audit every occurrence so the Phase-1 fail-open blast radius is
                # observable/alertable in production (it should only fire for internal
                # east-west traffic behind the gateway).
                self._logger.warning(
                    "principal_resolved_system",
                    reason="token_less_internal_call",
                    phase="1_fail_open",
                )
                return Principal.system()
            raise UnauthenticatedException("Authentication required")

        # (1) id only, from the JWT (signature verified upstream; local exp check inside).
        external_id = self.identity_token_reader.extract_user_uuid(token)
        if not external_id:
            raise UnauthenticatedException("Token missing or invalid user identity")

        # (2) Mirror-first: a known user never touches PM.
        record = self.user_repository.find_by_external_id(external_id)
        if record is not None:
            permissions = self._permissions_for(record.is_super, record.role_id)
            return Principal.for_user(
                user_id=record.user_id,
                external_id=record.external_id,
                role_code=record.role_code,
                is_super=record.is_super,
                permissions=permissions,
            )

        # Unknown user → mirror via PM (caller's own token); degrade on outage.
        return await self._resolve_new_user(external_id, token)

    async def _resolve_new_user(self, external_id: str, token: str) -> Principal:
        try:
            info = await self.fetch_current_user_info_api_client.fetch_current_user_info(token)
        except InvalidDataException as exc:
            # The PM client wraps every failure (network, timeout, malformed 200) into
            # InvalidDataException. Catch ONLY that so a genuine programming error still
            # surfaces as a 500 instead of masquerading as a PM outage.
            #
            # Observability: a true outage ("Failed to fetch…") and a reachable-but-corrupt
            # PM ("An error occurred…") both land here and both degrade a brand-new caller
            # to a transient default-`user` principal (Phase-1, bounded to reads). We emit
            # the raw `detail` so the two modes ARE distinguishable in logs/alerts. A
            # crisper split (distinct PM error types) is Phase-2 work in the shared PM
            # client, out of scope for this ticket.
            self._logger.warning(
                "principal_pm_degraded",
                external_id=external_id,
                subsystem="profile_management",
                error=type(exc).__name__,
                detail=str(exc),
                degraded_to="user_transient",
            )
            return self._transient_user_principal(external_id)

        email = info.email or None
        name = info.name or None
        user_id, role_code = self.user_repository.upsert_user(external_id, email=email, name=name)

        # Build the Principal from the roles catalog (stable seed data) via the returned
        # role_code — NOT by re-reading the just-written user row across a second session.
        # This keeps authz correctness independent of read-your-writes across sessions,
        # and still honours the (rare) case where the row was concurrently created +
        # promoted between our miss and our upsert (role_code reflects the persisted row).
        role = self.user_repository.find_role_by_code(role_code) if role_code else None
        is_super = role.is_super if role is not None else False
        role_id = role.role_id if role is not None else None
        return Principal.for_user(
            user_id=user_id,
            external_id=external_id,
            role_code=role_code,
            is_super=is_super,
            permissions=self._permissions_for(is_super, role_id),
        )

    def _permissions_for(self, is_super: bool, role_id: str | None) -> frozenset[str]:
        """Resolve the permission set to embed in a Principal.

        A super_admin bypasses the set entirely (`Principal.has` short-circuits on
        `is_super`), so we intentionally carry an EMPTY set for them — callers MUST go
        through `has()` / `bypass_pool_visibility`, never inspect `.permissions`
        directly. A user with no role (role_id None) likewise gets an empty set →
        deny-all (fail-closed).
        """
        if is_super or not role_id:
            return frozenset()
        return frozenset(self.user_repository.get_permissions_for_role(role_id))

    def _transient_user_principal(self, external_id: str) -> Principal:
        """Non-persisted default-`user` principal for the PM-outage-on-new-user case.

        Prefer the live seed of the `user` role (DB is up; only PM is down) so any UI
        edits to the user-role permissions are honoured; fall back to the constant
        default set if the role can't be read.
        """
        role = self.user_repository.find_role_by_code(USER_ROLE)
        if role is not None:
            permissions = frozenset(self.user_repository.get_permissions_for_role(role.role_id))
        else:
            permissions = DEFAULT_USER_PERMISSIONS
        return Principal.for_user(
            user_id=None,
            external_id=external_id,
            role_code=USER_ROLE,
            is_super=False,
            permissions=permissions,
        )
