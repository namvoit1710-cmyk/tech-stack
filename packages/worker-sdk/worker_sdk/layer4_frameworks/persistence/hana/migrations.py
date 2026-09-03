"""
HANA migration stub.

Idempotent migration function that creates / alters tables as needed.
"""
from typing import Any


def migrate(connection_manager: Any=None) -> None:
    """Run idempotent migrations. No-op while persistence is stubbed."""
    if connection_manager is None:
        return
    # Future: CREATE TABLE IF NOT EXISTS ...
