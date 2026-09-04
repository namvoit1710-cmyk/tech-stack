import asyncio

from smart_service_sdk.layer2_application.features.material_sds_analysis.use_cases.material_sds_analysis_usecase import (
    MaterialSdsAnalysisCommand,
    MaterialSdsAnalysisItem,
    MaterialSdsAnalysisOptions,
    MaterialSdsAnalysisUseCase,
)
from smart_service_sdk.layer2_application.features.runtime_configuration.runtime_configuration_service import (
    RuntimeConfigurationService,
)
from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationResponse,
)

_OUTPUT = (
    '{"decision": "required", "hazardous_categories": ["solvent"], '
    '"confidence": 0.9, "reasons": ["hazard indicators"], "evidence": ["model note"]}'
)


class _RecordingGenerationClient:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return GenerationResponse(output_text=self.output_text)


class _FakeRuntimeSettingRepository:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value

    async def get_many(self, keys: list[str]) -> dict[str, str]:
        return {key: self.values[key] for key in keys if key in self.values}


def _config_service(resource_urls_json: str, allowed_domains_json: str):
    return RuntimeConfigurationService(
        _FakeRuntimeSettingRepository(
            {
                "MATERIAL_SDS_RESOURCE_URLS": resource_urls_json,
                "MATERIAL_SDS_ALLOWED_DOMAINS": allowed_domains_json,
            }
        )
    )


def test_sds_check_restricts_web_search_and_cites_configured_sources() -> None:
    client = _RecordingGenerationClient(_OUTPUT)
    service = _config_service(
        '["https://echa.europa.eu"]', '["echa.europa.eu"]'
    )
    use_case = MaterialSdsAnalysisUseCase(
        generation_client=client, runtime_configuration_service=service
    )

    result = asyncio.run(
        use_case.execute(
            MaterialSdsAnalysisCommand(
                materials=[
                    MaterialSdsAnalysisItem(
                        material_name="Acetone",
                        options=MaterialSdsAnalysisOptions(enable_online_search=True),
                    )
                ]
            )
        )
    )

    request = client.requests[0]
    assert request.tools == (
        {"type": "web_search", "filters": {"allowed_domains": ["echa.europa.eu"]}},
    )
    assert request.tool_choice == "required"
    # Resource URL surfaced to the model as an authoritative reference.
    assert "https://echa.europa.eu" in request.messages[0].content
    # Sources reflected in the response evidence.
    evidence = result.results[0].evidence
    assert any("Authoritative references provided" in line for line in evidence)
    assert any("allowed domains" in line for line in evidence)


def test_sds_check_without_configured_domains_uses_open_web_tool() -> None:
    client = _RecordingGenerationClient(_OUTPUT)
    service = _config_service("[]", "[]")
    use_case = MaterialSdsAnalysisUseCase(
        generation_client=client, runtime_configuration_service=service
    )

    result = asyncio.run(
        use_case.execute(
            MaterialSdsAnalysisCommand(
                materials=[
                    MaterialSdsAnalysisItem(
                        material_name="Distilled Water",
                        options=MaterialSdsAnalysisOptions(enable_online_search=True),
                    )
                ]
            )
        )
    )

    assert client.requests[0].tools == ({"type": "web_search"},)
    # No configured sources -> no source-citation lines appended.
    assert all(
        "allowed domains" not in line for line in result.results[0].evidence
    )
