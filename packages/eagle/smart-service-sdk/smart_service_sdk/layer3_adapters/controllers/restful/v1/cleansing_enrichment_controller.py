from fastapi import APIRouter, Depends, Request

from smart_service_sdk.layer2_application.features.cleansing_enrichment.use_cases.cleansing_enrichment_usecase import (
    CleansingEnrichmentCommand,
    CleansingEnrichmentResult,
    CleansingEnrichmentUseCase,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto, BaseOutputDto

router = APIRouter(tags=["cleansing-enrichment"])


class CleansingEnrichmentInputDto(BaseInputDto):
    record_id: str
    values: dict = {}


class CleansingEnrichmentOutputDto(BaseOutputDto):
    result_id: str
    record_id: str
    proposed_values: dict
    confidence: float


def get_cleansing_usecase(request: Request) -> CleansingEnrichmentUseCase:
    return request.app.state.container["cleansing_enrichment_usecase"]


@router.post("/cleansing-enrichment/new-record", response_model=CleansingEnrichmentOutputDto)
async def cleansing_enrichment(payload: CleansingEnrichmentInputDto, use_case: CleansingEnrichmentUseCase = Depends(get_cleansing_usecase)):
    result: CleansingEnrichmentResult = await use_case.execute(payload.to_dataclass(CleansingEnrichmentCommand))
    return CleansingEnrichmentOutputDto.from_dataclass(result)
