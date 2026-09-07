from typing import TypeVar, Generic, List, Optional, Type
from sqlalchemy.future import select
from app.layer2_application.repositories.base_repository_interface import IBaseRepository


T = TypeVar('T')
OrmT = TypeVar('OrmT')


class BaseRepository(IBaseRepository[T], Generic[T, OrmT]):
    def __init__(self, session_factory, model_cls: Type[OrmT]):
        self.session_factory = session_factory
        self.model_cls = model_cls

    def to_domain(self, orm_entity: OrmT) -> T:
        raise NotImplementedError

    def to_orm(self, domain_entity: T) -> OrmT:
        raise NotImplementedError

    async def find_all(self) -> List[T]:
        async with self.session_factory() as session:
            result = await session.execute(select(self.model_cls))
            return [self.to_domain(r) for r in result.scalars().all()]

    async def find_by_id(self, id: str) -> Optional[T]:
        async with self.session_factory() as session:
            result = await session.execute(select(self.model_cls).filter_by(id=id))
            orm_entity = result.scalars().first()
            return self.to_domain(orm_entity) if orm_entity else None

    async def save(self, entity: T) -> T:
        orm_entity = self.to_orm(entity)
        async with self.session_factory() as session:
            session.add(orm_entity)
            await session.commit()
            await session.refresh(orm_entity)
            return self.to_domain(orm_entity)

    async def delete(self, id: str) -> bool:
        async with self.session_factory() as session:
            obj = await session.get(self.model_cls, id)
            if obj:
                await session.delete(obj)
                await session.commit()
                return True
            return False
