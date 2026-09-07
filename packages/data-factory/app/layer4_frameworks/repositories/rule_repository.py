from app.layer4_frameworks.repositories.base_repository import BaseRepository
from app.layer4_frameworks.orm.entities.rule_entity import RuleSetOrmEntity
from app.layer1_domain.entities.rule_management import RuleSet
from app.layer2_application.repositories.rule_repository_interface import IRuleRepository
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession


class RuleRepository(BaseRepository[RuleSet, RuleSetOrmEntity], IRuleRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        super().__init__(session_factory, RuleSetOrmEntity)

    def to_domain(self, orm_entity: RuleSetOrmEntity) -> RuleSet:
        return RuleSet(
            id=orm_entity.id,
            name=orm_entity.name,
            rules=orm_entity.rules,
            description=orm_entity.description,
            status=orm_entity.status,
            created_at=orm_entity.created_at.isoformat() if orm_entity.created_at else None
        )

    def to_orm(self, domain_entity: RuleSet) -> RuleSetOrmEntity:
        return RuleSetOrmEntity(
            id=domain_entity.id,
            name=domain_entity.name,
            rules=domain_entity.rules,
            description=domain_entity.description,
            status=domain_entity.status
        )
