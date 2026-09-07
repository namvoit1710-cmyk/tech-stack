from dataclasses import asdict
from typing import List, Dict, Any, Optional

from fastapi import APIRouter, HTTPException, Request, Body

from pydantic import field_validator

from app.layer3_adapters.controllers.restful.v1.dtos.base_dto import BaseInputDto
from app.layer3_adapters.controllers.restful.v1.dtos.rule_spec import (
    RuleSpecError,
    validate_schema_transform_rules,
)
from app.layer2_application.features.schema_transform.use_cases.schema_transform_usecase import (
    InspectSchemaCommand,
    GenerateSchemaMappingCommand,
    PreviewSchemaTransformCommand,
    ExecuteSchemaTransformCommand,
)


router = APIRouter()


class InspectSchemaInput(BaseInputDto):
    file_id: Optional[str] = None
    file_path: Optional[str] = None
    file_format: str = "csv"
    sample_size: int = 5
    version_id: Optional[str] = None
    sheet_names: Optional[List[str]] = None
    merge_sheets: bool = False
    add_sheet_name_column: bool = False
    header_row: Optional[int] = None


class GenerateSchemaMappingInput(BaseInputDto):
    source_schema: Dict[str, Any]
    target_schema: Dict[str, Any]
    source_type: str = "file"
    mapping_hints: Optional[List[Dict[str, Any]]] = None


class PreviewSchemaTransformInput(BaseInputDto):
    sample_data: List[Dict[str, Any]]
    rules: List[Dict[str, Any]]
    file_format: str = "json"
    target_schema: Optional[Dict[str, Any]] = None

    @field_validator("rules")
    @classmethod
    def _rules_are_executable(cls, value):
        try:
            return validate_schema_transform_rules(value)
        except RuleSpecError as exc:
            raise ValueError(str(exc)) from exc


class ExecuteSchemaTransformInput(BaseInputDto):
    source_file_id: Optional[str] = None
    file_path: Optional[str] = None
    rules: List[Dict[str, Any]]
    file_format: str = "csv"
    output_format: str = "csv"
    target_schema: Optional[Dict[str, Any]] = None
    field_mapping: Optional[List[Dict[str, Any]]] = None
    source_version_id: Optional[str] = None
    target_file_id: Optional[str] = None
    target_version_id: Optional[str] = None
    sheet_names: Optional[List[str]] = None
    merge_sheets: bool = False
    add_sheet_name_column: bool = False
    header_row: Optional[int] = None

    @field_validator("header_row")
    @classmethod
    def _header_row_is_a_row_index(cls, value):
        if value is not None and value < 0:
            raise ValueError("header_row must be a 0-based row index >= 0")
        return value

    @field_validator("rules")
    @classmethod
    def _rules_are_executable(cls, value):
        try:
            return validate_schema_transform_rules(value)
        except RuleSpecError as exc:
            raise ValueError(str(exc)) from exc


def get_usecase(request: Request):
    return request.app.state.container.get("schema_transform_usecase")


@router.post("/inspect")
async def inspect_schema(request: Request, dto: InspectSchemaInput = Body(...)):
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")
    file_id = dto.file_id or dto.file_path
    if not file_id:
        raise HTTPException(400, "file_id is required")

    command = InspectSchemaCommand(
        file_path=file_id,
        file_format=dto.file_format,
        sample_size=dto.sample_size,
        version_id=dto.version_id,
        sheet_names=dto.sheet_names,
        merge_sheets=dto.merge_sheets,
        add_sheet_name_column=dto.add_sheet_name_column,
        header_row=dto.header_row,
    )
    result = await usecase.inspect_file(command)
    if not result.success:
        raise HTTPException(400, result.message)
    return asdict(result)


@router.post("/generate-mapping")
async def generate_schema_mapping(
    request: Request, dto: GenerateSchemaMappingInput = Body(...)
):
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")

    command = GenerateSchemaMappingCommand(
        source_schema=dto.source_schema,
        target_schema=dto.target_schema,
        source_type=dto.source_type,
        mapping_hints=dto.mapping_hints,
    )
    result = await usecase.generate_mapping(command)
    if not result.success:
        raise HTTPException(400, result.message)
    return asdict(result)


@router.post("/preview")
async def preview_schema_transform(
    request: Request, dto: PreviewSchemaTransformInput = Body(...)
):
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")

    command = PreviewSchemaTransformCommand(
        sample_data=dto.sample_data,
        rules=dto.rules,
        file_format=dto.file_format,
        target_schema=dto.target_schema,
    )
    result = await usecase.preview_transform(command)
    if not result.success:
        raise HTTPException(400, result.message)
    return asdict(result)


@router.post("")
async def execute_schema_transform(
    request: Request, dto: ExecuteSchemaTransformInput = Body(...)
):
    usecase = get_usecase(request)
    if not usecase:
        raise HTTPException(500, "UseCase not wired")
    source_file_id = dto.source_file_id or dto.file_path
    if not source_file_id:
        raise HTTPException(400, "source_file_id is required")

    command = ExecuteSchemaTransformCommand(
        source_file_id=source_file_id,
        rules=dto.rules,
        file_format=dto.file_format,
        output_format=dto.output_format,
        target_schema=dto.target_schema,
        field_mapping=dto.field_mapping,
        source_version_id=dto.source_version_id,
        target_file_id=dto.target_file_id,
        target_version_id=dto.target_version_id,
        sheet_names=dto.sheet_names,
        merge_sheets=dto.merge_sheets,
        add_sheet_name_column=dto.add_sheet_name_column,
        header_row=dto.header_row,
    )
    result = await usecase.execute(command)
    if not result.success:
        if result.result and result.result.stale_version:
            raise HTTPException(
                409,
                {
                    "message": result.message,
                    "source_file_id": source_file_id,
                    "requested_version_id": dto.target_version_id,
                    "current_version_id": result.result.current_version_id,
                },
            )
        raise HTTPException(400, result.message)
    return asdict(result)
