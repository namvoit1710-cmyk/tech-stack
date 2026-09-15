"""Base ORM entity with SQLAlchemy declarative base."""
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String
from typing import Optional


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM entities."""
    pass


class BaseORMEntity(Base):
    """Abstract base ORM entity with id column."""
    __abstract__ = True

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
