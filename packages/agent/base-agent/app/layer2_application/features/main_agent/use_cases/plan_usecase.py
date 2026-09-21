from dataclasses import dataclass

from app.layer1_domain.entities.execution_plan import ExecutionPlan
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer2_application.interfaces.logger_interface import ILogger
from app.layer4_frameworks.clients.workflow_clients import IWorkflowClient


@dataclass
class PlanInputDto:
    workflow: WorkflowDefinition


@dataclass
class PlanOutputDto:
    execution_result: ExecutionPlan
    is_reviewed: bool


class PlanUseCase:
    def __init__(
        self,
        logger: ILogger,
        workflow_client: IWorkflowClient
    ):
        self._logger = logger
        self._workflow_client = workflow_client


    async def execute(self, planDto: PlanInputDto) -> PlanOutputDto:
        workflow = planDto.workflow

        workflow_info = await self._workflow_client.get_workflow_info(
            workflow.workflow_id
        )

        self._logger.info(f"Fetched workflow info for {workflow.workflow_id}: {workflow_info}")

        # Step 3: persist the plan as a strong typed entity, then serialize for state updates.
        plan = ExecutionPlan(
            workflow_id=workflow.workflow_id,
            workflow_info=workflow_info,
            steps=[
                "Validate the incoming workflow payload",
                "Build a sample execution result",
                "Review the synthesized output",
            ],
        )

        self._logger.info(f"Generated execution plan for workflow {workflow.workflow_id}: {plan}")

        return PlanOutputDto(
            execution_result=plan,
            is_reviewed=False,
        )