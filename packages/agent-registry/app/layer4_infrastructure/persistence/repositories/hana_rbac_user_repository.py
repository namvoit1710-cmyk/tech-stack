"""HANA RBAC user repository — user mirror keyed on external_id (SA RBAC v1, Task 2).

Since T5 this is the ONLY user repository (the legacy email-keyed HANAUserRepository was
removed in the contract migration). Reads/writes the `users` mirror and joins `roles` /
`role_permissions` / `permissions` for role + permission-set resolution.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from uuid6 import uuid7

from app.layer1_domain.rbac import USER_ROLE
from app.layer2_application.dtos.rbac_read_models import MirrorUser, RoleRef
from app.layer2_application.interfaces.rbac_user_repository_port import IRbacUserRepository
from app.layer2_application.interfaces.uuid_generator_port import IUUIDGeneratorPort
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.permission_model import PermissionModel
from app.layer4_infrastructure.persistence.models.role_model import RoleModel
from app.layer4_infrastructure.persistence.models.role_permission_model import RolePermissionModel
from app.layer4_infrastructure.persistence.models.user_model import UserModel


class HANARbacUserRepository(IRbacUserRepository):
    """HANA implementation of the RBAC user mirror repository."""

    def __init__(
        self,
        db_factory: DatabaseFactory,
        uuid_generator: IUUIDGeneratorPort | None = None,
    ):
        self.db_factory = db_factory
        # uuid_generator is optional so tests can inject a deterministic one; falls
        # back to the repo's uuidv7 generator to stay consistent with other tables.
        self._uuid_generator = uuid_generator

    # ----- reads -------------------------------------------------------------

    def find_by_external_id(self, external_id: str) -> MirrorUser | None:
        with self.db_factory.get_session() as session:
            row = session.execute(
                select(UserModel, RoleModel)
                .join(RoleModel, UserModel.role_id == RoleModel.id, isouter=True)
                .where(UserModel.external_id == external_id)
            ).first()
            if row is None:
                return None
            return self._to_mirror_user(row[0], row[1])

    def list_users(self) -> list[MirrorUser]:
        with self.db_factory.get_session() as session:
            rows = session.execute(
                select(UserModel, RoleModel).join(
                    RoleModel, UserModel.role_id == RoleModel.id, isouter=True
                )
            ).all()
            return [self._to_mirror_user(u, r) for (u, r) in rows]

    def count_users_with_super_role(self) -> int:
        with self.db_factory.get_session() as session:
            return int(
                session.execute(
                    select(func.count())
                    .select_from(UserModel)
                    .join(RoleModel, UserModel.role_id == RoleModel.id)
                    # NB: `== True` renders `= <bool literal>`; `.is_(True)` renders
                    # `IS true` — OK on SQLite but a HANA syntax error (IS is NULL/UNKNOWN
                    # only). Caught by the live-HANA run, not by the SQLite unit tests.
                    .where(RoleModel.is_super == True)  # noqa: E712
                ).scalar_one()
            )

    def get_permissions_for_role(self, role_id: str) -> set[str]:
        if not role_id:
            return set()
        with self.db_factory.get_session() as session:
            codes = session.execute(
                select(PermissionModel.code)
                .join(
                    RolePermissionModel,
                    RolePermissionModel.permission_id == PermissionModel.id,
                )
                .where(RolePermissionModel.role_id == role_id)
            ).scalars().all()
            return set(codes)

    def find_role_by_code(self, code: str) -> RoleRef | None:
        with self.db_factory.get_session() as session:
            role = session.execute(
                select(RoleModel).where(RoleModel.code == code)
            ).scalar_one_or_none()
            if role is None:
                return None
            return RoleRef(role_id=role.id, code=role.code, is_super=bool(role.is_super))

    # ----- writes ------------------------------------------------------------

    def upsert_user(
        self, external_id: str, email: str | None = None, name: str | None = None
    ) -> tuple[str, str | None]:
        with self.db_factory.get_session() as session:
            existing = self._row_by_external_id(session, external_id)

            if existing is not None:
                # Refresh cosmetic fields only; NEVER touch the role.
                changed = False
                if email is not None and existing.email != email:
                    existing.email = email
                    changed = True
                if name is not None and existing.name != name:
                    existing.name = name
                    changed = True
                if changed:
                    existing.updated_at = datetime.now(timezone.utc)
                session.flush()
                return existing.id, self._role_code(session, existing.role_id)

            # New user → default `user` role.
            user_role = session.execute(
                select(RoleModel).where(RoleModel.code == USER_ROLE)
            ).scalar_one_or_none()
            role_id = user_role.id if user_role is not None else None

            user_id = self._new_id()
            session.add(
                UserModel(
                    id=user_id,
                    external_id=external_id,
                    # email is nullable since 20260716_02 (T5): store NULL when PM has no
                    # email for the user, rather than a "" placeholder.
                    email=email,
                    name=name,
                    role_id=role_id,
                )
            )
            try:
                session.flush()
            except IntegrityError:
                # Concurrent insert for the same external_id → fall back to the row
                # the other transaction created (M1 race, mirrors agent `save`).
                session.rollback()
                winner = self._row_by_external_id(session, external_id)
                if winner is None:
                    raise
                return winner.id, self._role_code(session, winner.role_id)
            # Read the persisted role code so both upsert paths return DB-derived data.
            return user_id, self._role_code(session, role_id)

    def set_role(self, user_id: str, role_id: str) -> None:
        with self.db_factory.get_session() as session:
            user = session.get(UserModel, user_id)
            if user is None:
                return
            user.role_id = role_id
            user.updated_at = datetime.now(timezone.utc)
            session.flush()

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _row_by_external_id(session, external_id: str) -> UserModel | None:
        """Fetch the ORM row for an external_id within an open session (or None)."""
        return session.execute(
            select(UserModel).where(UserModel.external_id == external_id)
        ).scalar_one_or_none()

    @staticmethod
    def _role_code(session, role_id: str | None) -> str | None:
        if not role_id:
            return None
        role = session.get(RoleModel, role_id)
        return role.code if role is not None else None

    @staticmethod
    def _to_mirror_user(user: UserModel, role: RoleModel | None) -> MirrorUser:
        return MirrorUser(
            user_id=user.id,
            external_id=user.external_id,
            email=user.email,
            name=user.name,
            role_id=user.role_id,
            role_code=role.code if role is not None else None,
            is_super=bool(role.is_super) if role is not None else False,
        )

    def _new_id(self) -> str:
        if self._uuid_generator is not None:
            return str(self._uuid_generator.generate_uuid())
        # Repo convention: uuidv7 for new rows (matches the T1 seed).
        return str(uuid7())
