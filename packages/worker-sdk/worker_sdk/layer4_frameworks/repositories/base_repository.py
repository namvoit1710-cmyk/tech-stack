"""
Base repository implementation stub.

This would contain the generic SQLAlchemy CRUD implementation
that concrete repositories inherit from.
"""
from typing import Any


class BaseRepository:
    """Stub repository base. Implement when a real ORM is wired."""

    async def find_all(self) -> Any:
        raise NotImplementedError("BaseRepository is a stub.")

    async def find_by_id(self, entity_id: str) -> Any:
        raise NotImplementedError("BaseRepository is a stub.")

    async def save(self, entity: Any) -> Any:
        raise NotImplementedError("BaseRepository is a stub.")
