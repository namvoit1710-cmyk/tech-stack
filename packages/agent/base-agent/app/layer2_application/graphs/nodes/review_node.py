from app.layer1_domain.entities.state import MainState
from app.layer2_application.features.main_agent.use_cases import get_usecase
from app.layer2_application.features.main_agent.use_cases.review_usecase import ReviewInputDto, WorkflowExecutionResult


async def review_node(state: MainState, deps: dict) -> dict:
    usecase = get_usecase("review")
    input_dto = ReviewInputDto(
        execution_result=WorkflowExecutionResult.from_mapping(state.get("execution_result")),
    )
    return await usecase.execute(input_dto)
