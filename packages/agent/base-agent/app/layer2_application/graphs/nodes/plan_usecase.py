from app.layer1_domain.entities.state import MainState
from app.layer1_domain.entities.workflow import WorkflowDefinition
from app.layer2_application.features.main_agent.use_cases import get_usecase
from app.layer2_application.features.main_agent.use_cases.plan_usecase import PlanInputDto


async def plan_node(state: MainState) -> dict:
    usecase = get_usecase("plan")
    input_dto = PlanInputDto(
        workflow=WorkflowDefinition.from_mapping(state.get("parameters", {}).get("workflow")),
    )
    return await usecase.execute(input_dto)
