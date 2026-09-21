from dataclasses import asdict, dataclass

from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.state import MainState
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer1_domain.entities.workflow_execution_result import WorkflowExecutionResult
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer4_frameworks.clients.workflow_clients import IWorkflowClient


@dataclass
class ExecuteInputDto:
    workflow: WorkflowDefinition
    plan: ExecutionPlan


@dataclass
class ExecuteOutputDto:
    execution_result: WorkflowExecutionResult


class ExecuteUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_client: IWorkflowClient
    ):
        self._logger = logger
        self._workflow_client = workflow_client


    async def execute(self, executeDto: ExecuteInputDto) -> ExecuteOutputDto:
        workflow = executeDto.workflow
        plan = executeDto.plan

        self._logger.info(f"Using execution plan for workflow {workflow.workflow_id}: {plan}")

        trigger_workflow_response = await self._workflow_client.trigger_workflow(
            workflow.workflow_id
        )

        execution_result = WorkflowExecutionResult(
            workflow_id=workflow.workflow_id,
            trigger_response=trigger_workflow_response,
            completed_steps=plan.steps,
        )

        self._logger.info(f"Execution result for workflow {workflow.workflow_id}: {execution_result}")

        return ExecuteOutputDto(
            execution_result=execution_result
        )