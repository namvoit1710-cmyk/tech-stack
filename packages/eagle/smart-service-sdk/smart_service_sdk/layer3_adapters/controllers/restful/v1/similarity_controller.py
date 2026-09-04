from pydantic import Field
from fastapi import APIRouter, Depends, Request

from smart_service_sdk.layer2_application.features.similarity.use_cases.similarity_config_usecases import (
    GetSimilarityConfigUseCase,
    UpdateSimilarityConfigCommand,
    UpdateSimilarityConfigUseCase,
)
from smart_service_sdk.layer2_application.features.similarity.use_cases.similarity_usecase import (
    SimilarityCommand,
    SimilarityResult,
    SimilarityUseCase,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import (
    BaseInputDto,
    BaseOutputDto,
)

router = APIRouter(tags=["similarity"])


class SimilarityInputDto(BaseInputDto):
    query_text: str
    mode: str = "ALL"
    tenant_id: str | None = None
    top_k: int | None = None
    filters: dict[str, object] = Field(default_factory=dict)


class RetrievalCandidateDto(BaseOutputDto):
    document_id: str
    chunk_id: str
    content: str
    score: float
    route: str
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "RetrievalCandidateDto":
        return cls(
            document_id=dataclass_obj.document_id,
            chunk_id=dataclass_obj.chunk_id,
            content=dataclass_obj.content,
            score=dataclass_obj.score,
            route=dataclass_obj.route.value,
            metadata=dict(dataclass_obj.metadata),
        )


class SimilarityOutputDto(BaseOutputDto):
    tenant_id: str
    mode: str
    candidates: list[RetrievalCandidateDto]

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "SimilarityOutputDto":
        return cls(
            tenant_id=dataclass_obj.tenant_id,
            mode=dataclass_obj.mode,
            candidates=[
                RetrievalCandidateDto.from_dataclass(candidate)
                for candidate in dataclass_obj.candidates
            ],
        )


class SimilarityConfigOutputDto(BaseOutputDto):
    vector_top_k: int
    vector_min_score: float
    graph_seed_top_k: int
    graph_neighbor_cap_per_seed: int
    graph_max_relation_candidates: int
    graph_max_graph_chunk_candidates: int
    max_results: int


class SimilarityConfigInputDto(BaseInputDto):
    vector_top_k: int
    vector_min_score: float
    graph_seed_top_k: int
    graph_neighbor_cap_per_seed: int
    graph_max_relation_candidates: int
    graph_max_graph_chunk_candidates: int
    max_results: int


def get_similarity_usecase(request: Request) -> SimilarityUseCase:
    return request.app.state.container["similarity_usecase"]


def get_similarity_config_usecase(request: Request) -> GetSimilarityConfigUseCase:
    return request.app.state.container["get_similarity_config_usecase"]


def get_update_similarity_config_usecase(
    request: Request,
) -> UpdateSimilarityConfigUseCase:
    return request.app.state.container["update_similarity_config_usecase"]


@router.post("/similarity", response_model=SimilarityOutputDto)
async def similarity(
    payload: SimilarityInputDto,
    use_case: SimilarityUseCase = Depends(get_similarity_usecase),
):
    result: SimilarityResult = await use_case.execute(
        SimilarityCommand(
            query_text=payload.query_text,
            mode=payload.mode,
            tenant_id=payload.tenant_id,
            top_k=payload.top_k,
            filters=payload.filters,
        )
    )
    return SimilarityOutputDto.from_dataclass(result)


@router.get("/similarity/config", response_model=SimilarityConfigOutputDto)
async def get_similarity_config(
    use_case: GetSimilarityConfigUseCase = Depends(get_similarity_config_usecase),
):
    result = await use_case.execute()
    return SimilarityConfigOutputDto.from_dataclass(result)


@router.put("/similarity/config", response_model=SimilarityConfigOutputDto)
async def update_similarity_config(
    payload: SimilarityConfigInputDto,
    use_case: UpdateSimilarityConfigUseCase = Depends(
        get_update_similarity_config_usecase
    ),
):
    result = await use_case.execute(
        UpdateSimilarityConfigCommand(
            vector_top_k=payload.vector_top_k,
            vector_min_score=payload.vector_min_score,
            graph_seed_top_k=payload.graph_seed_top_k,
            graph_neighbor_cap_per_seed=payload.graph_neighbor_cap_per_seed,
            graph_max_relation_candidates=payload.graph_max_relation_candidates,
            graph_max_graph_chunk_candidates=payload.graph_max_graph_chunk_candidates,
            max_results=payload.max_results,
        )
    )
    return SimilarityConfigOutputDto.from_dataclass(result)
