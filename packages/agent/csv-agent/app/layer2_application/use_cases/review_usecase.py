import logging
from dataclasses import dataclass

from app.layer1_domain.entities.csv_execution_result import CSVExecutionResult

logger = logging.getLogger(__name__)


@dataclass
class ReviewInputDto:
    """Input DTO for Review Use Case."""

    execution_result: CSVExecutionResult


@dataclass
class ReviewOutputDto:
    """Output DTO for Review Use Case."""

    is_reviewed: bool
    feedback: str
    success: bool


class ReviewUseCase:
    """
    Use case for reviewing execution results.

    Analyzes the execution result and provides feedback on success/failure.
    """

    def __init__(self, logger_instance=None):
        """Initialize with optional logger."""
        self.logger = logger_instance or logger

    async def execute(self, input_dto: ReviewInputDto) -> ReviewOutputDto:
        """
        Review the execution result and provide feedback.

        Args:
            input_dto: Contains execution result to review

        Returns:
            ReviewOutputDto with review status and feedback
        """
        result = input_dto.execution_result

        logger.info(f"Reviewing execution result: {result.success}")

        # Analyze results
        total_steps = len(result.step_results)
        successful_steps = sum(1 for step in result.step_results if step.success)
        failed_steps = total_steps - successful_steps

        # Build feedback message
        if result.success:
            feedback = f"All {total_steps} steps completed successfully."
            if result.final_file_id:
                feedback += f" Final file ID: {result.final_file_id}"
        else:
            feedback = f" {failed_steps} of {total_steps} steps failed."

            # Add details about failed steps
            failed_step_details = []
            for step in result.step_results:
                if not step.success:
                    failed_step_details.append(
                        f"  - Step {step.order} ({step.tool}): {step.error}"
                    )

            if failed_step_details:
                feedback += "\n\nFailed steps:\n" + "\n".join(failed_step_details)

        # Add summary if available
        if result.summary:
            feedback += f"\n\nSummary: {result.summary}"

        logger.info(f"Review complete: success={result.success}")

        return ReviewOutputDto(
            is_reviewed=result.success, feedback=feedback, success=result.success
        )
