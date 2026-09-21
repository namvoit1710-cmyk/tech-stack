from typing import Protocol
from app.layer2_application.interfaces.review_generator_interface import IReviewGenerator
from app.layer1_domain.entities.review_context import ReviewContextType


class IReviewerResolver(Protocol):

    def resolve(
        self,
        review_context_type: ReviewContextType,
    ) -> IReviewGenerator:
        ...