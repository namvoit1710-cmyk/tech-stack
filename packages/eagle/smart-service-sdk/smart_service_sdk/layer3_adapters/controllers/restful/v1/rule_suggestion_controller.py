from fastapi import APIRouter, Depends, Request

from smart_service_sdk.layer2_application.features.rule_suggestion.use_cases.rule_suggestion_usecase import (
    RuleSuggestionCommand,
    RuleSuggestionResult,
    RuleSuggestionUseCase,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto, BaseOutputDto

router = APIRouter(tags=["rule-suggestion"])


class RuleSuggestionInputDto(BaseInputDto):
    request_id: str
    change_summary: str


class RuleSuggestionOutputDto(BaseOutputDto):
    result_id: str
    request_id: str
    suggestions: list[str]
    rationale: str


def get_usecase(request: Request) -> RuleSuggestionUseCase:
    return request.app.state.container["rule_suggestion_usecase"]


@router.post("/rule-suggestions", response_model=RuleSuggestionOutputDto)
async def suggest_rules(payload: RuleSuggestionInputDto, use_case: RuleSuggestionUseCase = Depends(get_usecase)):
    result: RuleSuggestionResult = await use_case.execute(payload.to_dataclass(RuleSuggestionCommand))
    return RuleSuggestionOutputDto.from_dataclass(result)
