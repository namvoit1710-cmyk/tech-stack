from abc import ABC, abstractmethod
from app.layer2_application.interfaces.plan_generator_interface import IPlanGenerator
from app.layer1_domain.entities.planning_context import PlanningContextType


class IPlannerResolver(ABC):

    @abstractmethod
    def resolve(
        self,
        planning_context_type: PlanningContextType,
    ) -> IPlanGenerator:
        ...