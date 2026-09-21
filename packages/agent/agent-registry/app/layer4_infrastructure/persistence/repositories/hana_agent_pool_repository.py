"""HANA agent-pool repository (SA RBAC v1, Task 4).

Pools are the aggregate root and are SOFT-deleted (`status`); all reads exclude
`status='deleted'`. On soft delete the junction rows are cleared explicitly (FK cascade
does not fire on a soft delete). Agent/member links are idempotent.
"""

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from uuid6 import uuid7

from app.layer2_application.dtos.rbac_read_models import AgentPoolDetail
from app.layer2_application.interfaces.agent_pool_repository_port import IAgentPoolRepository
from app.layer4_infrastructure.persistence.db.database import DatabaseFactory
from app.layer4_infrastructure.persistence.models.agent_pool_agent_model import AgentPoolAgentModel
from app.layer4_infrastructure.persistence.models.agent_pool_member_model import AgentPoolMemberModel
from app.layer4_infrastructure.persistence.models.agent_pool_model import AgentPoolModel

STATUS_ACTIVE = "active"
STATUS_DELETED = "deleted"


class HANAAgentPoolRepository(IAgentPoolRepository):
    """HANA implementation of the agent-pool repository."""

    def __init__(self, db_factory: DatabaseFactory):
        self.db_factory = db_factory

    # ----- pool CRUD ---------------------------------------------------------

    def create_pool(self, name: str, description: str | None, created_by: str | None) -> AgentPoolDetail:
        with self.db_factory.get_session() as session:
            pool = AgentPoolModel(
                id=str(uuid7()),
                name=name,
                description=description,
                status=STATUS_ACTIVE,
                created_by=created_by,
            )
            session.add(pool)
            session.flush()
            return self._to_detail(pool)

    def get_pool(self, pool_id: str) -> AgentPoolDetail | None:
        with self.db_factory.get_session() as session:
            pool = self._active(session, pool_id)
            return self._to_detail(pool) if pool is not None else None

    def list_pools(self) -> list[AgentPoolDetail]:
        with self.db_factory.get_session() as session:
            rows = session.execute(
                select(AgentPoolModel)
                .where(AgentPoolModel.status != STATUS_DELETED)
                .order_by(AgentPoolModel.created_at)
            ).scalars().all()
            return [self._to_detail(p) for p in rows]

    def list_pools_for_user(self, user_id: str) -> list[AgentPoolDetail]:
        if not user_id:
            return []
        with self.db_factory.get_session() as session:
            rows = session.execute(
                select(AgentPoolModel)
                .join(AgentPoolMemberModel, AgentPoolMemberModel.pool_id == AgentPoolModel.id)
                .where(
                    AgentPoolMemberModel.user_id == user_id,
                    AgentPoolModel.status != STATUS_DELETED,
                )
                .order_by(AgentPoolModel.created_at)
            ).scalars().all()
            return [self._to_detail(p) for p in rows]

    def update_pool(self, pool_id: str, name: str, description: str | None) -> AgentPoolDetail | None:
        with self.db_factory.get_session() as session:
            pool = self._active(session, pool_id)
            if pool is None:
                return None
            pool.name = name
            pool.description = description
            pool.updated_at = datetime.now(timezone.utc)
            session.flush()
            return self._to_detail(pool)

    def soft_delete_pool(self, pool_id: str) -> None:
        with self.db_factory.get_session() as session:
            pool = self._active(session, pool_id)
            if pool is None:
                return
            pool.status = STATUS_DELETED
            pool.updated_at = datetime.now(timezone.utc)
            # FK cascade won't fire on a soft delete → clear junction rows explicitly.
            session.execute(delete(AgentPoolAgentModel).where(AgentPoolAgentModel.pool_id == pool_id))
            session.execute(delete(AgentPoolMemberModel).where(AgentPoolMemberModel.pool_id == pool_id))
            session.flush()

    # ----- agent / member links (idempotent) --------------------------------

    def add_agent(self, pool_id: str, agent_id: str) -> None:
        with self.db_factory.get_session() as session:
            if session.get(AgentPoolAgentModel, (pool_id, agent_id)) is not None:
                return  # fast path: sequential re-add is a no-op
            session.add(AgentPoolAgentModel(pool_id=pool_id, agent_id=agent_id))
            try:
                session.flush()
            except IntegrityError:
                # Two concurrent adds of the same (pool_id, agent_id) both pass the
                # check-then-insert above; the second hits the composite PK. The link
                # now exists, so treat this as an idempotent success rather than a 500
                # (mirrors agent `save` / rbac `upsert_user`).
                session.rollback()

    def remove_agent(self, pool_id: str, agent_id: str) -> None:
        with self.db_factory.get_session() as session:
            session.execute(
                delete(AgentPoolAgentModel).where(
                    AgentPoolAgentModel.pool_id == pool_id,
                    AgentPoolAgentModel.agent_id == agent_id,
                )
            )
            session.flush()

    def add_member(self, pool_id: str, user_id: str) -> None:
        with self.db_factory.get_session() as session:
            if session.get(AgentPoolMemberModel, (pool_id, user_id)) is not None:
                return  # fast path: sequential re-add is a no-op
            session.add(AgentPoolMemberModel(pool_id=pool_id, user_id=user_id))
            try:
                session.flush()
            except IntegrityError:
                # Two concurrent adds of the same (pool_id, user_id) both pass the
                # check-then-insert above; the second hits the composite PK. The link
                # now exists, so treat this as an idempotent success rather than a 500
                # (mirrors agent `save` / rbac `upsert_user`).
                session.rollback()

    def remove_member(self, pool_id: str, user_id: str) -> None:
        with self.db_factory.get_session() as session:
            session.execute(
                delete(AgentPoolMemberModel).where(
                    AgentPoolMemberModel.pool_id == pool_id,
                    AgentPoolMemberModel.user_id == user_id,
                )
            )
            session.flush()

    def is_member(self, pool_id: str, user_id: str) -> bool:
        if not user_id:
            return False
        with self.db_factory.get_session() as session:
            return session.get(AgentPoolMemberModel, (pool_id, user_id)) is not None

    def list_agent_ids(self, pool_id: str) -> list[str]:
        with self.db_factory.get_session() as session:
            return list(session.execute(
                select(AgentPoolAgentModel.agent_id).where(AgentPoolAgentModel.pool_id == pool_id)
            ).scalars().all())

    def list_member_ids(self, pool_id: str) -> list[str]:
        with self.db_factory.get_session() as session:
            return list(session.execute(
                select(AgentPoolMemberModel.user_id).where(AgentPoolMemberModel.pool_id == pool_id)
            ).scalars().all())

    # ----- visibility --------------------------------------------------------

    def find_agent_ids_visible_to(self, user_id: str) -> set[str]:
        if not user_id:
            return set()
        with self.db_factory.get_session() as session:
            rows = session.execute(
                select(AgentPoolAgentModel.agent_id)
                .join(
                    AgentPoolMemberModel,
                    AgentPoolMemberModel.pool_id == AgentPoolAgentModel.pool_id,
                )
                .join(AgentPoolModel, AgentPoolModel.id == AgentPoolAgentModel.pool_id)
                .where(
                    AgentPoolMemberModel.user_id == user_id,
                    AgentPoolModel.status != STATUS_DELETED,
                )
                .distinct()
            ).scalars().all()
            return set(rows)

    # ----- helpers -----------------------------------------------------------

    @staticmethod
    def _active(session, pool_id: str) -> AgentPoolModel | None:
        pool = session.get(AgentPoolModel, pool_id)
        if pool is None or pool.status == STATUS_DELETED:
            return None
        return pool

    @staticmethod
    def _to_detail(pool: AgentPoolModel) -> AgentPoolDetail:
        return AgentPoolDetail(
            id=pool.id,
            name=pool.name,
            description=pool.description,
            status=pool.status,
            created_by=pool.created_by,
            created_at=pool.created_at,
            updated_at=pool.updated_at,
        )
