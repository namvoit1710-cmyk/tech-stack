"""Base audit ORM entity with timestamp columns."""
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import String
from typing import Optional

from app.layer4_frameworks.orm.entities.base_entity import BaseORMEntity


class BaseAuditORMEntity(BaseORMEntity):
    """Abstract ORM entity with audit timestamps."""
    __abstract__ = True

    created_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    updated_at: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
