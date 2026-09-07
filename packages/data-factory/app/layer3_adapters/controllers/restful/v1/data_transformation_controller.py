from fastapi import APIRouter, HTTPException, Request, Body, Query
from typing import List, Dict, Any, Optional
from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto
from app.layer2_application.features.data_transformation.use_cases.data_transformation_usecase import (
    DataTransformationCommand
)
from app.layer2_application.features.data_transformation.use_cases.row_transformation_usecase import (
    RowTransformationCommand, RowTransformationUseCase
)
from dataclasses import asdict


router = APIRouter()


class TransformCloudFileInput(BaseInputDto):
    file_id: Optional[str] = None
    file_path: Optional[str] = None
    file_format: str = "csv"
    output_format: str = "csv"
    rules: List[Dict[str, Any]]
    version_id: Optional[str] = None


class TransformRowsInput(BaseInputDto):
    file_id: Optional[str] = None
    file_path: Optional[str] = None
    file_format: str = "csv"
    operations: List[Dict[str, Any]]
    use_column_indices: bool = False
    version_id: Optional[str] = None


def get_usecase(request: Request):
    return request.app.state.container.get("data_transformation_usecase")


def get_row_transformation_usecase(request: Request):
    return request.app.state.container.get("row_transformation_usecase")


@router.post("")
async def transform_data(request: Request, dto: TransformCloudFileInput = Body(...)):
    file_id = dto.file_id or dto.file_path
    if not file_id:
        raise HTTPException(400, "file_id is required")
    usecase = get_usecase(request)
    command = DataTransformationCommand(
        file_id=file_id,
        file_format=dto.file_format,
        output_format=dto.output_format,
        rules=dto.rules,
        version_id=dto.version_id,
    )
    result = await usecase.execute(command)
    if not result.success:
        if result.result and result.result.stale_version:
            raise HTTPException(
                409,
                {
                    "message": result.message,
                    "file_id": file_id,
                    "requested_version_id": dto.version_id,
                    "current_version_id": result.result.current_version_id,
                },
            )
        raise HTTPException(400, result.message)
    return asdict(result)


@router.post("/rows")
async def transform_rows(request: Request, dto: TransformRowsInput = Body(...)):
    """
    Perform row-level transformations (insert/delete/update) on a cloud file.

    Supports three row identification methods:
    - index: by row position
    - condition: by expression evaluation
    - values: by exact value matching
    """
    try:
        file_id = dto.file_id or dto.file_path
        if not file_id:
            raise ValueError("file_id is required")

        # Validate input
        if not dto.operations:
            raise ValueError("At least one operation is required")

        if len(dto.operations) > 100:  # Reasonable limit
            raise ValueError("Maximum 100 operations allowed per request")

        usecase = get_row_transformation_usecase(request)
        command = RowTransformationCommand(
            file_path=file_id,
            file_format=dto.file_format,
            operations=dto.operations,
            use_column_indices=dto.use_column_indices,
            version_id=dto.version_id,
        )
        result = await usecase.execute(command)
        if not result.success:
            raise HTTPException(400, result.message)

        return {
            "success": result.result.success,
            "affected_rows": result.result.affected_rows,
            "total_rows": result.result.total_rows,
            "message": result.result.message,
            "preview_data": result.result.preview_data,
            "download_url": result.result.download_url
        }

    except ValueError as e:
        raise HTTPException(400, f"Invalid input: {str(e)}")
    except Exception as e:
        raise HTTPException(500, f"Row transformation failed: {str(e)}")


@router.get("/result/query")
async def query_transform_result(request: Request):
    try:
        usecase = get_usecase(request)
        import urllib.parse
        q = urllib.parse.unquote_plus(str(request.url.query))
        parts = q.split('&')
        odata_parts = [p for p in parts if p.startswith('odata=') or p.startswith('$')]
        f_part = next((p for p in parts if p.startswith('file_id=')), None)
        legacy_f_part = next((p for p in parts if p.startswith('file_name=')), None)
        version_part = next((p for p in parts if p.startswith('version_id=')), None)

        if not f_part and not legacy_f_part:
            f_param = request.query_params.get("file_id") or request.query_params.get("file_name")
            if not f_param:
                raise HTTPException(400, "file_id required")
            file_id = f_param
        else:
            raw_part = f_part or legacy_f_part
            file_id = raw_part.split('=', 1)[1]

        version_id = None
        if version_part:
            version_id = version_part.split('=', 1)[1]
        else:
            version_id = request.query_params.get("version_id")

        odata = "&".join(odata_parts).replace("odata=", "")
        return usecase.query_result_file(file_id, odata, version_id=version_id)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/result/download")
async def download_result(
    request: Request,
    file_id: Optional[str] = Query(None),
    file_name: Optional[str] = Query(None),
    version_id: Optional[str] = Query(None),
):
    try:
        usecase = get_usecase(request)
        resolved_file_id = file_id or file_name
        if not resolved_file_id:
            raise HTTPException(400, "file_id required")
        url = usecase.get_download_url(resolved_file_id, version_id=version_id)
        return {"file_url": url}
    except Exception as e:
        raise HTTPException(500, str(e))
