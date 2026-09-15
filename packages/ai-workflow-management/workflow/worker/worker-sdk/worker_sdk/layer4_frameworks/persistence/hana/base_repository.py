"""
Base HANA repository stub.

Provides raw-SQL CRUD helpers for SAP HANA when persistence is wired.
"""

from typing import Any, Protocol


class IDatabaseConnection(Protocol):
    """Minimal DB connection protocol."""

    def cursor(self) -> None: ...
    def commit(self) -> None: ...
    def close(self) -> None: ...


class BaseHanaRepository:
    """Stub HANA repository base."""

    def __init__(self, connection_manager: Any=None) -> None:
        self.connection_manager = connection_manager
