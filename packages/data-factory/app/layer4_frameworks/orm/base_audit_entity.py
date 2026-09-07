from sqlalchemy import Column, DateTime
from datetime import datetime, UTC
from .base_entity import BaseOrmEntity


class BaseAuditOrmEntity(BaseOrmEntity):
    __abstract__ = True
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, nullable=True, onupdate=lambda: datetime.now(UTC))
