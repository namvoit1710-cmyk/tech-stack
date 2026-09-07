from sqlalchemy import Column, String
import uuid
from .config.database_config import Base


class BaseOrmEntity(Base):
    __abstract__ = True
    id = Column(String, primary_key=True, default=lambda: str(getattr(uuid, 'uuid7', uuid.uuid4)()))
