from typing import Protocol
from app.layer1_domain.entities.review_context import ReviewContextType
from app.layer1_domain.entities.reviewer import ReviewResponse

class IReviewGenerator(Protocol):

    async def generate(self, context: ReviewContextType) -> ReviewResponse:
        ...