from dataclasses import dataclass

from app.layer1_domain.entities.workflow_execution_result import WorkflowExecutionResult
from app.layer2_application.interfaces.logger_interface import ILogger


@dataclass
class ReviewInputDto:
    execution_result: WorkflowExecutionResult


@dataclass
class ReviewOutputDto:
    message: str
    agent_result: dict
    agent_data: WorkflowExecutionResult
    is_reviewed: bool


class ReviewUseCase:
    def __init__(
        self,
        logger: ILogger,
    ):
        self._logger = logger


    async def execute(self, reviewDto: ReviewInputDto) -> ReviewOutputDto:
        execution_result = reviewDto.execution_result
        workflow_id = execution_result.workflow_id
        summary = f"Execution finished on workflow {workflow_id}."

        self._logger.info(f"Review result for workflow {workflow_id}: {summary}")

        return ReviewOutputDto(
            message=summary,
            agent_result={
                "content": summary,
            },
            agent_data=execution_result,
            is_reviewed=True,
        )
