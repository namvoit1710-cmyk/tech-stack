from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel

from app.layer2_application.features.import_data.use_cases.import_data_usecase import (
    ImportDataCommand,
    ImportDataUseCase,
)
from app.layer2_application.features.import_data.use_cases.upload_import_file_usecase import (
    UploadImportFileCommand,
    UploadImportFileUseCase,
)

router = APIRouter(tags=["governance-import"])


class GovernanceImportInputDto(BaseModel):
    file_ids: list[str]
    tenant_id: str | None = None


class GovernanceImportOutputDto(BaseModel):
    job_id: str
    tenant_id: str
    status: str
    file_ids: list[str]
    accepted_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    error_message: str = ""
    file_results: list[dict[str, object]]


class GovernanceUploadOutputDto(BaseModel):
    result_id: str
    file_name: str
    content_hash: str
    tenant_id: str | None = None
    row_count: int
    raw_headers: list[str]
    parsed_rows: list[dict[str, str]]
    created_at: str | None = None


def get_import_data_usecase(request: Request) -> ImportDataUseCase:
    return request.app.state.container["import_data_usecase"]


def get_upload_import_file_usecase(request: Request) -> UploadImportFileUseCase:
    return request.app.state.container["upload_import_file_usecase"]


@router.post(
    "/governance/import-data",
    response_model=GovernanceImportOutputDto,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_data(
    payload: GovernanceImportInputDto,
    use_case: ImportDataUseCase = Depends(get_import_data_usecase),
):
    try:
        result = await use_case.execute(
            ImportDataCommand(file_ids=payload.file_ids, tenant_id=payload.tenant_id)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return GovernanceImportOutputDto(**result.__dict__)


@router.post(
    "/governance/import-data/upload",
    response_model=GovernanceUploadOutputDto,
    status_code=status.HTTP_201_CREATED,
)
async def upload_import_file(
    file: UploadFile = File(...),
    tenant_id: str | None = Form(default=None),
    use_case: UploadImportFileUseCase = Depends(get_upload_import_file_usecase),
):
    try:
        result = await use_case.execute(
            UploadImportFileCommand(
                file_name=file.filename or "",
                content=file.file,
                tenant_id=tenant_id,
            )
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        await file.close()
    return GovernanceUploadOutputDto(**result.__dict__)
