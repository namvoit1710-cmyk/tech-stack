from dataclasses import dataclass
from typing import Literal, Union, Optional
from app.layer1_domain.entities.review_context import ReviewStatus

@dataclass
class ReviewResponse():
    evaluation: ReviewStatus
    reason: str
    nextstep: str | None = None
    
    @classmethod
    def from_dict(cls, data: dict) -> "ReviewResponse":
        raw_eval = data.get("evaluation")
        try:
            eval_status = ReviewStatus(raw_eval)
        except ValueError:
            raise ValueError(f"Value error for 'evaluation': '{raw_eval}' is not a valid ReviewStatus.")

        return cls(
            evaluation=eval_status,
            nextstep=data.get("nextstep"),
            reason=data.get("reason", "")
        )