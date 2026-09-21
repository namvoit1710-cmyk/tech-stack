from app.layer1_domain.entities.state import MainState
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer2_application.features.main_agent.use_cases import get_usecase
from app.layer2_application.features.main_agent.use_cases.execute_usecase import ExecuteInputDto, ExecutionPlan


async def execute_node(state: MainState) -> dict:
    usecase = get_usecase("execute")
    input_dto = ExecuteInputDto(
        workflow=WorkflowDefinition.from_mapping(state.get("parameters", {}).get("workflow")),
        plan=ExecutionPlan.from_mapping(state.get("plan"))
    )
    return await usecase.execute(input_dto)
