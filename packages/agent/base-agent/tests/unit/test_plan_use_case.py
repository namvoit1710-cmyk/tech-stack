import asyncio

from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer2_application.features.main_agent.use_cases.plan_usecase import PlanInputDto, PlanUseCase


class _FakeLogger:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def error(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def warning(self, message: str, **kwargs) -> None:
        self.messages.append(message)

    def debug(self, message: str, **kwargs) -> None:
        self.messages.append(message)


class _FakeWorkflowClient:
    async def get_workflow_info(self, workflow_id: str) -> dict:
        return {"workflow_id": workflow_id, "status": "mock"}


def test_plan_use_case_returns_execution_plan_with_review_flag_false():
    logger = _FakeLogger()
    use_case = PlanUseCase(logger=logger, workflow_client=_FakeWorkflowClient())

    result = asyncio.run(
        use_case.execute(
            PlanInputDto(
                workflow=WorkflowDefinition(workflow_id="wf-plan-1"),
            )
        )
    )

    assert result.execution_result.workflow_id == "wf-plan-1"
    assert result.execution_result.workflow_info == {
        "workflow_id": "wf-plan-1",
        "status": "mock",
    }
    assert result.execution_result.steps
    assert result.is_reviewed is False
    assert logger.messages