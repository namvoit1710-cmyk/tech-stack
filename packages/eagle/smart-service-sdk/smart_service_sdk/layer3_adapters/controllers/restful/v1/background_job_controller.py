from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from smart_service_sdk.layer2_application.features.background_job.use_cases.background_job_usecases import (
    BackgroundJobFileResult,
    BackgroundJobResult,
    CreateBackgroundJobCommand,
    CreateBackgroundJobUseCase,
    GetBackgroundJobCommand,
    GetBackgroundJobUseCase,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import (
    BaseInputDto,
    BaseOutputDto,
)

router = APIRouter(tags=["import-data-background-job"])


class BackgroundJobInputDto(BaseInputDto):
    file_ids: list[str]
    tenant_id: str | None = None


class BackgroundJobFileResultDto(BaseOutputDto):
    file_id: str
    status: str
    row_count: int
    started_at: str | None = None
    ended_at: str | None = None
    error_message: str
    metadata: dict[str, object]

    @classmethod
    def from_dataclass(
        cls,
        dataclass_obj: BackgroundJobFileResult,
    ) -> "BackgroundJobFileResultDto":
        return cls(**dataclass_obj.__dict__)


class BackgroundJobOutputDto(BaseOutputDto):
    job_id: str
    tenant_id: str
    status: str
    file_ids: list[str]
    accepted_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    error_message: str
    file_results: list[BackgroundJobFileResultDto]

    @classmethod
    def from_dataclass(
        cls,
        dataclass_obj: BackgroundJobResult,
    ) -> "BackgroundJobOutputDto":
        return cls(
            job_id=dataclass_obj.job_id,
            tenant_id=dataclass_obj.tenant_id,
            status=dataclass_obj.status,
            file_ids=list(dataclass_obj.file_ids),
            accepted_at=dataclass_obj.accepted_at,
            started_at=dataclass_obj.started_at,
            ended_at=dataclass_obj.ended_at,
            error_message=dataclass_obj.error_message,
            file_results=[
                BackgroundJobFileResultDto.from_dataclass(item)
                for item in dataclass_obj.file_results
            ],
        )


def get_create_background_job_usecase(request: Request) -> CreateBackgroundJobUseCase:
    return request.app.state.container["create_background_job_usecase"]


def get_background_job_status_usecase(request: Request) -> GetBackgroundJobUseCase:
    return request.app.state.container["get_background_job_usecase"]


@router.post(
    "/import-jobs",
    response_model=BackgroundJobOutputDto,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_background_job(
    payload: BackgroundJobInputDto,
    use_case: CreateBackgroundJobUseCase = Depends(get_create_background_job_usecase),
):
    try:
        result = await use_case.execute(
            CreateBackgroundJobCommand(
                file_ids=payload.file_ids,
                tenant_id=payload.tenant_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return BackgroundJobOutputDto.from_dataclass(result)


@router.get(
    "/import-jobs/{job_id}",
    response_model=BackgroundJobOutputDto,
)
async def get_background_job(
    job_id: str,
    tenant_id: str | None = Query(default=None),
    use_case: GetBackgroundJobUseCase = Depends(get_background_job_status_usecase),
):
    try:
        result = await use_case.execute(
            GetBackgroundJobCommand(job_id=job_id, tenant_id=tenant_id)
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return BackgroundJobOutputDto.from_dataclass(result)
