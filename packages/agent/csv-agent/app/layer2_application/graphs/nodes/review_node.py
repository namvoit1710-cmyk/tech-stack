from app.layer1_domain.entities.csv_execution_result import CSVExecutionResult
from app.layer1_domain.entities.file_state import FileState
from app.layer2_application.use_cases.review_usecase import ReviewInputDto


async def review_node(state: FileState, deps: dict) -> dict:
    usecase = deps.get("review_usecase")
    if usecase is None:
        raise RuntimeError("review_usecase dependency is not configured")
    execution_result = CSVExecutionResult.from_dict(state.get("execution_result", {}))
    execution_result.validate()
    output_dto = await usecase.execute(
        ReviewInputDto(execution_result=execution_result)
    )
    review_iteration = int(state.get("review_iteration", 0) or 0) + 1
    return {
        "is_reviewed": output_dto.is_reviewed,
        "review_message": output_dto.feedback,
        "review_iteration": review_iteration,
    }
