import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.layer1_domain.entities.csv_execution_result import (
    CSVExecutionResult,
    StepResult,
)
from app.layer2_application.graphs.edges.review_outcome import make_route_after_review
from app.layer2_application.graphs.nodes.review_node import review_node
from app.layer2_application.use_cases.review_usecase import (
    ReviewInputDto,
    ReviewOutputDto,
    ReviewUseCase,
)


def test_review_usecase_sets_is_reviewed_false_when_execution_failed():
    usecase = ReviewUseCase()
    execution_result = CSVExecutionResult(
        success=False,
        step_results=[
            StepResult(
                order=1, tool="transform_column", success=False, result={}, error="boom"
            )
        ],
        summary="failed",
    )

    output = asyncio.run(
        usecase.execute(ReviewInputDto(execution_result=execution_result))
    )

    assert output.is_reviewed is False
    assert output.success is False
    assert "transform_column" in output.feedback


def test_review_usecase_feedback_includes_final_file_for_success():
    usecase = ReviewUseCase()
    execution_result = CSVExecutionResult(
        success=True,
        step_results=[
            StepResult(
                order=1, tool="query_file_data", success=True, result={"rows": []}
            )
        ],
        final_file_id="file_out",
        summary="done",
    )

    output = asyncio.run(
        usecase.execute(ReviewInputDto(execution_result=execution_result))
    )

    assert output.is_reviewed is True
    assert "file_out" in output.feedback
    assert "Summary: done" in output.feedback


def test_route_after_review_returns_plan_when_failed_and_within_retry_budget():
    state = {"is_reviewed": False, "review_iteration": 1, "max_review_iterations": 3}
    assert make_route_after_review()(state) == "plan"


def test_route_after_review_ends_when_failed_and_retry_budget_exhausted():
    state = {"is_reviewed": False, "review_iteration": 3, "max_review_iterations": 3}
    assert make_route_after_review()(state) == "__end__"


def test_route_after_review_ends_when_reviewed():
    assert make_route_after_review()({"is_reviewed": True}) == "__end__"


def test_review_node_increments_review_iteration_counter():
    mock_usecase = SimpleNamespace(
        execute=AsyncMock(
            return_value=ReviewOutputDto(
                is_reviewed=False, feedback="failed", success=False
            )
        )
    )
    state = {
        "review_iteration": 1,
        "execution_result": {
            "success": False,
            "error": "failed",
            "step_results": [
                {"order": 1, "tool": "t", "success": False, "result": {}, "error": "x"}
            ],
        },
    }

    result = asyncio.run(review_node(state, deps={"review_usecase": mock_usecase}))

    assert result["is_reviewed"] is False
    assert result["review_message"] == "failed"
    assert result["review_iteration"] == 2


def test_review_node_requires_injected_usecase():
    try:
        asyncio.run(review_node({"execution_result": {}}, deps={}))
    except RuntimeError as exc:
        assert "review_usecase" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")
