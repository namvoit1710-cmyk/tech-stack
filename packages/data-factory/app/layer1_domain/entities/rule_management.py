from dataclasses import dataclass, field
from typing import List, Dict, Any
from app.layer1_domain.base_audit_entity import BaseAuditDomainEntity


@dataclass(kw_only=True)
class RuleSet(BaseAuditDomainEntity):
    name: str
    rules: List[Dict[str, Any]] = field(default_factory=list)
    description: str = ""
    status: str = "active"

    @classmethod
    def create(cls, name: str, rules: List[Dict[str, Any]], description: str = "") -> 'RuleSet':
        return cls(name=name, rules=rules, description=description)
