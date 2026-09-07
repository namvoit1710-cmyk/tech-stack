from sqlalchemy import Column, String, JSON
from app.layer4_frameworks.orm.base_audit_entity import BaseAuditOrmEntity


class RuleSetOrmEntity(BaseAuditOrmEntity):
    __tablename__ = "rule_sets"

    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    status = Column(String, default="active")
    rules = Column(JSON, nullable=False, default=list)
