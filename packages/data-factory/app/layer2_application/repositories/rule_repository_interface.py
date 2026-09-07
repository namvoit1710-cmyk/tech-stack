from app.layer2_application.repositories.base_repository_interface import IBaseRepository
from app.layer1_domain.entities.rule_management import RuleSet


class IRuleRepository(IBaseRepository[RuleSet]):
    pass
    pass
