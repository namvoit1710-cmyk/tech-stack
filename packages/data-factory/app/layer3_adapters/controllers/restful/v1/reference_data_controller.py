from fastapi import APIRouter, HTTPException, Request, Body
from typing import Optional, List
from dataclasses import asdict
from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto
from app.layer2_application.features.reference_data.use_cases.build_reference_keyset_usecase import (
    BuildReferenceKeysetCommand,
)


router = APIRouter()


class BuildReferenceKeysetInput(BaseInputDto):
    source_file_path: str
    source_format: str = "csv"
    key_columns: Optional[List[str]] = None
    multi_value_columns: Optional[List[str]] = None
    multi_value_delimiter: str = "|"
    hazard_filter_column: Optional[str] = None
    hazard_keywords: Optional[List[str]] = None


def get_usecase(request: Request):
    return request.app.state.container.get("build_reference_keyset_usecase")


@router.post("/keyset")
async def build_reference_keyset(request: Request, dto: BuildReferenceKeysetInput = Body(...)):
    """Materialize a large source file into a compact, de-duplicated parquet key-set."""
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")

    command = BuildReferenceKeysetCommand(
        source_file_path=dto.source_file_path,
        source_format=dto.source_format,
        key_columns=dto.key_columns or [],
        multi_value_columns=dto.multi_value_columns or [],
        multi_value_delimiter=dto.multi_value_delimiter,
        hazard_filter_column=dto.hazard_filter_column,
        hazard_keywords=dto.hazard_keywords,
    )
    result = await usecase.execute(command)
    if not result.success:
        raise HTTPException(400, result.message)
    return asdict(result)
