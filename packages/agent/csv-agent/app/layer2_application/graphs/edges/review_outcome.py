from __future__ import annotations

from collections.abc import Callable

from app.layer1_domain.entities.file_state import FileState

_DEFAULT_MAX_REVIEW_ITERATIONS = 3


def _coerce_positive_iteration_limit(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        parsed = int(value)
    elif isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        parsed = int(value)
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return default
    else:
        return default
    return parsed if parsed > 0 else default


def _route_after_review_with_default(
    state: FileState,
    *,
    default_max_review_iterations: int,
) -> str:
    if state.get("is_reviewed") is False:
        review_iteration = int(state.get("review_iteration", 0) or 0)
        default_limit = _coerce_positive_iteration_limit(
            default_max_review_iterations,
            default=_DEFAULT_MAX_REVIEW_ITERATIONS,
        )
        max_review_iterations = _coerce_positive_iteration_limit(
            state.get("max_review_iterations", default_limit),
            default=default_limit,
        )
        return "__end__" if review_iteration >= max_review_iterations else "plan"

    return "__end__"


def make_route_after_review(
    default_max_review_iterations: int = _DEFAULT_MAX_REVIEW_ITERATIONS,
) -> Callable[[FileState], str]:
    """Create the review router with the deployment-configured retry limit."""

    def _route_after_review(state: FileState) -> str:
        return _route_after_review_with_default(
            state,
            default_max_review_iterations=default_max_review_iterations,
        )

    return _route_after_review
