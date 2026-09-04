from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from smart_service_sdk.layer2_application.features.field_configuration.use_cases.field_config_usecases import (
    GetFieldConfigUseCase,
    UpdateFieldConfigCommand,
    UpdateFieldConfigUseCase,
)
from smart_service_sdk.layer2_application.features.field_configuration.use_cases.reindex_usecases import (
    ReindexFilesCommand,
    ReindexFilesUseCase,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationError,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import (
    BaseInputDto,
    BaseOutputDto,
)

router = APIRouter(tags=["ingest-fields"])


class FieldConfigOutputDto(BaseOutputDto):
    embedding_fields: list[str] = Field(default_factory=list)
    graph_entity_fields: list[str] = Field(default_factory=list)

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "FieldConfigOutputDto":
        return cls(
            embedding_fields=list(dataclass_obj.embedding_fields),
            graph_entity_fields=list(dataclass_obj.graph_entity_fields),
        )


class FieldConfigInputDto(BaseInputDto):
    embedding_fields: list[str] = Field(default_factory=list)
    graph_entity_fields: list[str] = Field(default_factory=list)
    # Optional: when the caller knows the available columns (e.g. from an
    # uploaded file) the server validates the selection is a subset of them.
    available_fields: list[str] = Field(default_factory=list)


class ReindexInputDto(BaseInputDto):
    file_ids: list[str] = Field(default_factory=list)
    tenant_id: str | None = None


class ReindexOutputDto(BaseOutputDto):
    tenant_id: str
    reindexed_file_ids: list[str] = Field(default_factory=list)
    failed_file_ids: list[str] = Field(default_factory=list)

    @classmethod
    def from_dataclass(cls, dataclass_obj) -> "ReindexOutputDto":
        return cls(
            tenant_id=dataclass_obj.tenant_id,
            reindexed_file_ids=list(dataclass_obj.reindexed_file_ids),
            failed_file_ids=list(dataclass_obj.failed_file_ids),
        )


def get_field_config_usecase(request: Request) -> GetFieldConfigUseCase:
    return request.app.state.container["get_field_config_usecase"]


def get_update_field_config_usecase(request: Request) -> UpdateFieldConfigUseCase:
    return request.app.state.container["update_field_config_usecase"]


def get_reindex_files_usecase(request: Request) -> ReindexFilesUseCase:
    return request.app.state.container["reindex_files_usecase"]


@router.get("/ingest-fields/config", response_model=FieldConfigOutputDto)
async def get_field_config(
    use_case: GetFieldConfigUseCase = Depends(get_field_config_usecase),
):
    result = await use_case.execute()
    return FieldConfigOutputDto.from_dataclass(result)


@router.put("/ingest-fields/config", response_model=FieldConfigOutputDto)
async def update_field_config(
    payload: FieldConfigInputDto,
    use_case: UpdateFieldConfigUseCase = Depends(get_update_field_config_usecase),
):
    try:
        result = await use_case.execute(
            UpdateFieldConfigCommand(
                embedding_fields=payload.embedding_fields,
                graph_entity_fields=payload.graph_entity_fields,
                available_fields=payload.available_fields,
            )
        )
    except RuntimeConfigurationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FieldConfigOutputDto.from_dataclass(result)


@router.post("/ingest-fields/reindex", response_model=ReindexOutputDto)
async def reindex_files(
    payload: ReindexInputDto,
    use_case: ReindexFilesUseCase = Depends(get_reindex_files_usecase),
):
    result = await use_case.execute(
        ReindexFilesCommand(file_ids=payload.file_ids, tenant_id=payload.tenant_id)
    )
    return ReindexOutputDto.from_dataclass(result)
