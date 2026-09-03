from typing import Optional, Protocol, TypeVar

T = TypeVar("T")


class IBaseRepository(Protocol[T]):
    """Base repository interface for persistence operations."""

    async def find_all(self) -> list[T]:
        """Retrieve all entities."""
        ...

    async def find_by_id(self, entity_id: str) -> Optional[T]:
        """Retrieve an entity by its ID."""
        ...

    async def save(self, entity: T) -> T:
        """Persist an entity (create or update)."""
        ...
