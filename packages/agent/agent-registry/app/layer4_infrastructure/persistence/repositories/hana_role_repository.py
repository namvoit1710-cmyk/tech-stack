"""HANA role repository — roles + role→permission mappings (SA RBAC v1, Task 3)."""

from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from uuid6 import uuid7

from app.layer2_application.dtos.rbac_read_models import RoleDetail
from app.layer2_application.interfaces.role_repository_port import IRoleRepository
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.permission_model import PermissionModel
from app.layer4_infrastructure.persistence.models.role_model import RoleModel
from app.layer4_infrastructure.persistence.models.role_permission_model import RolePermissionModel
from app.layer4_infrastructure.persistence.models.user_model import UserModel


class HANARoleRepository(IRoleRepository):
    """HANA implementation of the role repository."""

    def __init__(self, db_factory: DatabaseFactory):
        self.db_factory = db_factory

    # ----- reads -------------------------------------------------------------

    def list_roles(self) -> list[RoleDetail]:
        with self.db_factory.get_session() as session:
            roles = session.execute(select(RoleModel).order_by(RoleModel.code)).scalars().all()
            pairs = session.execute(
                select(RolePermissionModel.role_id, PermissionModel.code).join(
                    PermissionModel, RolePermissionModel.permission_id == PermissionModel.id
                )
            ).all()
            codes_by_role: dict[str, list[str]] = {}
            for role_id, code in pairs:
                codes_by_role.setdefault(role_id, []).append(code)
            return [self._to_detail(r, codes_by_role.get(r.id, [])) for r in roles]

    def get_role(self, role_id: str) -> RoleDetail | None:
        with self.db_factory.get_session() as session:
            role = session.get(RoleModel, role_id)
            if role is None:
                return None
            return self._to_detail(role, self._codes_for(session, role_id))

    def get_role_by_code(self, code: str) -> RoleDetail | None:
        with self.db_factory.get_session() as session:
            role = session.execute(
                select(RoleModel).where(RoleModel.code == code)
            ).scalar_one_or_none()
            if role is None:
                return None
            return self._to_detail(role, self._codes_for(session, role.id))

    def is_role_in_use(self, role_id: str) -> bool:
        with self.db_factory.get_session() as session:
            count = session.execute(
                select(func.count()).select_from(UserModel).where(UserModel.role_id == role_id)
            ).scalar_one()
            return int(count) > 0

    # ----- writes ------------------------------------------------------------

    def create_role(self, code: str, name: str, description: str | None) -> RoleDetail:
        with self.db_factory.get_session() as session:
            role = RoleModel(
                id=str(uuid7()),
                code=code,
                name=name,
                description=description,
                is_system=False,
                is_super=False,
            )
            session.add(role)
            session.flush()
            return self._to_detail(role, [])

    def update_role(self, role_id: str, name: str, description: str | None) -> None:
        with self.db_factory.get_session() as session:
            role = session.get(RoleModel, role_id)
            if role is None:
                return
            role.name = name
            role.description = description
            role.updated_at = datetime.now(timezone.utc)
            session.flush()

    def set_role_permissions(self, role_id: str, permission_ids: list[str]) -> None:
        with self.db_factory.get_session() as session:
            # Replace the whole set: clear then re-insert (junction rows are hard-deleted).
            session.execute(
                delete(RolePermissionModel).where(RolePermissionModel.role_id == role_id)
            )
            for pid in dict.fromkeys(permission_ids):  # de-dup, preserve order
                session.add(RolePermissionModel(role_id=role_id, permission_id=pid))
            session.flush()

    def delete_role(self, role_id: str) -> None:
        with self.db_factory.get_session() as session:
            role = session.get(RoleModel, role_id)
            if role is None:
                return
            session.delete(role)  # FK CASCADE clears role_permissions
            session.flush()

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _codes_for(session, role_id: str) -> list[str]:
        return session.execute(
            select(PermissionModel.code)
            .join(RolePermissionModel, RolePermissionModel.permission_id == PermissionModel.id)
            .where(RolePermissionModel.role_id == role_id)
        ).scalars().all()

    @staticmethod
    def _to_detail(role: RoleModel, codes: list[str]) -> RoleDetail:
        return RoleDetail(
            id=role.id,
            code=role.code,
            name=role.name,
            description=role.description,
            is_system=bool(role.is_system),
            is_super=bool(role.is_super),
            permission_codes=tuple(sorted(codes)),
        )
