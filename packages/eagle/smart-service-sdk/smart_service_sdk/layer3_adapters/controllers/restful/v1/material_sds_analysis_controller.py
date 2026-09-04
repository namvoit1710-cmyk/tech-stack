from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import Field

from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_analysis_usecase import (
    MaterialSdsAnalysisCommand,
    MaterialSdsAnalysisItem,
    MaterialSdsAnalysisItemResult,
    MaterialSdsAnalysisOptions,
    MaterialSdsAnalysisResult,
    MaterialSdsAnalysisUseCase,
)
from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_config_usecases import (
    GetMaterialSdsSourceConfigUseCase,
    MaterialSdsSourceConfigValidationError,
    UpdateMaterialSdsSourceConfigCommand,
    UpdateMaterialSdsSourceConfigUseCase,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    MaterialSdsSourceConfig,
)
from smart_service_sdk.layer3_adapters.controllers.restful.v1.dtos.base_dto import (
    BaseInputDto,
    BaseOutputDto,
)

router = APIRouter(tags=["material-sds-analysis"])


class MaterialSdsAnalysisOptionsInputDto(BaseInputDto):
    enable_online_search: bool = False


class MaterialSdsAnalysisItemInputDto(BaseInputDto):
    material_name: str = ""
    material_type: str = ""
    material_group: str = ""
    material_description: str = ""
    free_text_description: str = ""
    unspsc_code: str = ""
    classification: str = ""
    manufacturer_name: str = ""
    manufacturer_description: str = ""
    options: MaterialSdsAnalysisOptionsInputDto = Field(
        default_factory=MaterialSdsAnalysisOptionsInputDto
    )

    def to_command_item(self) -> MaterialSdsAnalysisItem:
        return MaterialSdsAnalysisItem(
            material_name=self.material_name,
            material_type=self.material_type,
            material_group=self.material_group,
            material_description=self.material_description,
            free_text_description=self.free_text_description,
            unspsc_code=self.unspsc_code,
            classification=self.classification,
            manufacturer_name=self.manufacturer_name,
            manufacturer_description=self.manufacturer_description,
            options=MaterialSdsAnalysisOptions(
                enable_online_search=self.options.enable_online_search
            ),
        )


class MaterialSdsAnalysisInputDto(BaseInputDto):
    materials: list[MaterialSdsAnalysisItemInputDto] = Field(
        ..., min_length=1
    )


class MaterialSdsAnalysisItemOutputDto(BaseOutputDto):
    material_index: int
    decision: str
    is_sds_required: bool
    hazardous_categories: list[str]
    confidence: float
    reasons: list[str]
    evidence: list[str]
    web_search_used: bool
    web_search_status: str
    method: str

    @classmethod
    def from_dataclass(
        cls, dataclass_obj: MaterialSdsAnalysisItemResult
    ) -> "MaterialSdsAnalysisItemOutputDto":
        return cls(
            material_index=dataclass_obj.material_index,
            decision=dataclass_obj.decision,
            is_sds_required=dataclass_obj.is_sds_required,
            hazardous_categories=list(dataclass_obj.hazardous_categories),
            confidence=dataclass_obj.confidence,
            reasons=list(dataclass_obj.reasons),
            evidence=list(dataclass_obj.evidence),
            web_search_used=dataclass_obj.web_search_used,
            web_search_status=dataclass_obj.web_search_status,
            method=dataclass_obj.method,
        )


class MaterialSdsAnalysisOutputDto(BaseOutputDto):
    results: list[MaterialSdsAnalysisItemOutputDto]

    @classmethod
    def from_dataclass(
        cls, dataclass_obj: MaterialSdsAnalysisResult
    ) -> "MaterialSdsAnalysisOutputDto":
        return cls(
            results=[
                MaterialSdsAnalysisItemOutputDto.from_dataclass(item)
                for item in dataclass_obj.results
            ]
        )


class MaterialSdsSourceConfigOutputDto(BaseOutputDto):
    resource_urls: list[str]
    allowed_domains: list[str]

    @classmethod
    def from_dataclass(
        cls, dataclass_obj: MaterialSdsSourceConfig
    ) -> "MaterialSdsSourceConfigOutputDto":
        return cls(
            resource_urls=list(dataclass_obj.resource_urls),
            allowed_domains=list(dataclass_obj.allowed_domains),
        )


class MaterialSdsSourceConfigInputDto(BaseInputDto):
    resource_urls: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)


def get_usecase(request: Request) -> MaterialSdsAnalysisUseCase:
    return request.app.state.container["material_sds_analysis_usecase"]


def get_material_sds_config_usecase(
    request: Request,
) -> GetMaterialSdsSourceConfigUseCase:
    return request.app.state.container["get_material_sds_source_config_usecase"]


def get_update_material_sds_config_usecase(
    request: Request,
) -> UpdateMaterialSdsSourceConfigUseCase:
    return request.app.state.container["update_material_sds_source_config_usecase"]


@router.post("/material-sds-analysis", response_model=MaterialSdsAnalysisOutputDto)
async def analyze_material_sds(
    payload: MaterialSdsAnalysisInputDto,
    use_case: MaterialSdsAnalysisUseCase = Depends(get_usecase),
):
    result = await use_case.execute(
        MaterialSdsAnalysisCommand(
            materials=[item.to_command_item() for item in payload.materials]
        )
    )
    return MaterialSdsAnalysisOutputDto.from_dataclass(result)


@router.get(
    "/material-sds-analysis/config",
    response_model=MaterialSdsSourceConfigOutputDto,
)
async def get_material_sds_config(
    use_case: GetMaterialSdsSourceConfigUseCase = Depends(
        get_material_sds_config_usecase
    ),
):
    result = await use_case.execute()
    return MaterialSdsSourceConfigOutputDto.from_dataclass(result)


@router.put(
    "/material-sds-analysis/config",
    response_model=MaterialSdsSourceConfigOutputDto,
)
async def update_material_sds_config(
    payload: MaterialSdsSourceConfigInputDto,
    use_case: UpdateMaterialSdsSourceConfigUseCase = Depends(
        get_update_material_sds_config_usecase
    ),
):
    try:
        result = await use_case.execute(
            UpdateMaterialSdsSourceConfigCommand(
                resource_urls=payload.resource_urls,
                allowed_domains=payload.allowed_domains,
            )
        )
    except MaterialSdsSourceConfigValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return MaterialSdsSourceConfigOutputDto.from_dataclass(result)
