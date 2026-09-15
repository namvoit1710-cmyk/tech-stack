"""
Database session manager stub.

In a real implementation this would configure SQLAlchemy (for dev/SQLite)
or SAP HANA (for production). Currently a no-op placeholder so that
the blueprint directory structure is in place.
"""
from typing import Any


class DatabaseSessionManager:
    """Stub session manager — wire a real engine in bootstrap when needed."""

    def __init__(self, connection_url: str = "sqlite:///dev.db") -> None:
        self.connection_url = connection_url

    async def get_session(self) -> Any:
        raise NotImplementedError("DatabaseSessionManager is a stub.")
