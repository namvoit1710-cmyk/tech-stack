from typing import Literal

from pydantic import Field
from fastapi import APIRouter, Depends, Request

from smart_service_sdk.layer2_application.features.search.use_cases.search_config_usecases import (
    GetSearchConfigUseCase,
    UpdateSearchConfigCommand,
    UpdateSearchConfigUseCase,
)
from smart_service_sdk.layer2_application.features.search.use_cases.search_usecase import (
    SearchCommand,
    SearchItem,
    SearchResult,
    SearchUseCase,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import (
    BaseInputDto,
    BaseOutputDto,
)

router = APIRouter(tags=["search"])


class SearchRuleInputDto(BaseInputDto):
    key: str
    value: str
    rule: Literal["exact", "fuzzy"]


class RetrievalCandidateDto(BaseOutputDto):
    document_id: str
    chunk_id: str
    content: str
    score: float
    exact_matches: list[str] = Field(default_factory=list)
    fuzzy_matches: dict[str, float] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "RetrievalCandidateDto":
        metadata = dict(dataclass_obj.metadata)
        return cls(
            document_id=dataclass_obj.document_id,
            chunk_id=dataclass_obj.chunk_id,
            content=dataclass_obj.content,
            score=dataclass_obj.score,
            exact_matches=list(metadata.pop("exact_matches", [])),
            fuzzy_matches=dict(metadata.pop("fuzzy_matches", {})),
            metadata=metadata,
        )


class SearchOutputDto(BaseOutputDto):
    tenant_id: str
    candidates: list[RetrievalCandidateDto]

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "SearchOutputDto":
        return cls(
            tenant_id=dataclass_obj.tenant_id,
            candidates=[
                RetrievalCandidateDto.from_dataclass(candidate)
                for candidate in dataclass_obj.candidates
            ],
        )


class SearchConfigOutputDto(BaseOutputDto):
    fuzzy_threshold: float
    max_results: int
    expand_terms_enabled: bool


class SearchConfigInputDto(BaseInputDto):
    fuzzy_threshold: float
    max_results: int
    expand_terms_enabled: bool


def get_search_usecase(request: Request) -> SearchUseCase:
    return request.app.state.container["search_usecase"]


def get_search_config_usecase(request: Request) -> GetSearchConfigUseCase:
    return request.app.state.container["get_search_config_usecase"]


def get_update_search_config_usecase(request: Request) -> UpdateSearchConfigUseCase:
    return request.app.state.container["update_search_config_usecase"]


@router.post("/search", response_model=SearchOutputDto)
async def search(
    payload: list[SearchRuleInputDto],
    use_case: SearchUseCase = Depends(get_search_usecase),
):
    result: SearchResult = await use_case.execute(
        SearchCommand(
            items=[
                SearchItem(
                    key=item.key,
                    value=item.value,
                    rule=str(item.rule).strip().lower(),
                )
                for item in payload
            ]
        )
    )
    return SearchOutputDto.from_dataclass(result)


@router.get("/search/config", response_model=SearchConfigOutputDto)
async def get_search_config(
    use_case: GetSearchConfigUseCase = Depends(get_search_config_usecase),
):
    result = await use_case.execute()
    return SearchConfigOutputDto.from_dataclass(result)


@router.put("/search/config", response_model=SearchConfigOutputDto)
async def update_search_config(
    payload: SearchConfigInputDto,
    use_case: UpdateSearchConfigUseCase = Depends(get_update_search_config_usecase),
):
    result = await use_case.execute(
        UpdateSearchConfigCommand(
            fuzzy_threshold=payload.fuzzy_threshold,
            max_results=payload.max_results,
            expand_terms_enabled=payload.expand_terms_enabled,
        )
    )
    return SearchConfigOutputDto.from_dataclass(result)
