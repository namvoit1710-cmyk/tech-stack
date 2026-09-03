"""
Base audit ORM entity stub.

Extends BaseORMEntity with audit columns (created_at, updated_at, etc.)
when a real ORM is wired.
"""

from worker_sdk.layer4_frameworks.orm.entities.base_entity import BaseORMEntity


class BaseAuditORMEntity(BaseORMEntity):
    """Stub audit ORM base. Adds audit columns when persistence is wired."""
    pass
