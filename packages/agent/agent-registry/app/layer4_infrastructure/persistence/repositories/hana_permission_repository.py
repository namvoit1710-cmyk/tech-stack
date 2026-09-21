"""HANA permission repository — the app permission catalog (SA RBAC v1, Task 3)."""

from sqlalchemy import select

from app.layer2_application.dtos.rbac_read_models import PermissionCatalogItem
from app.layer2_application.interfaces.permission_repository_port import IPermissionRepository
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.permission_model import PermissionModel


class HANAPermissionRepository(IPermissionRepository):
    """HANA implementation of the permission catalog reader."""

    def __init__(self, db_factory: DatabaseFactory):
        self.db_factory = db_factory

    def list_permissions(self) -> list[PermissionCatalogItem]:
        with self.db_factory.get_session() as session:
            rows = session.execute(
                select(PermissionModel).order_by(PermissionModel.code)
            ).scalars().all()
            return [
                PermissionCatalogItem(id=p.id, code=p.code, description=p.description)
                for p in rows
            ]

    def find_ids_for_codes(self, codes: list[str]) -> dict[str, str]:
        if not codes:
            return {}
        with self.db_factory.get_session() as session:
            rows = session.execute(
                select(PermissionModel.code, PermissionModel.id).where(
                    PermissionModel.code.in_(list(set(codes)))
                )
            ).all()
            return {code: pid for (code, pid) in rows}
