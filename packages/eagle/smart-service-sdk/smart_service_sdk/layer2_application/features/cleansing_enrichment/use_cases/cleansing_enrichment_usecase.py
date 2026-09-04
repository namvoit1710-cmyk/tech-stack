from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from smart_service_sdk.layer1_domain.entities.cleansing_result import CleansingResult
from smart_service_sdk.layer2_application.interfaces.llm_service_interface import ILlmServiceClient
from smart_service_sdk.layer2_application.interfaces.logger_interface import ILogger
from smart_service_sdk.layer2_application.repositories.result_repository_interface import IResultRepository


@dataclass
class CleansingEnrichmentCommand:
    record_id: str
    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class CleansingEnrichmentResult:
    result_id: str
    record_id: str
    proposed_values: dict[str, Any]
    confidence: float


class CleansingEnrichmentUseCase:
    def __init__(
        self,
        logger: ILogger,
        llm_service: ILlmServiceClient,
        result_repository: IResultRepository,
    ):
        self.logger = logger
        self.llm_service = llm_service
        self.result_repository = result_repository

    async def execute(self, command: CleansingEnrichmentCommand) -> CleansingEnrichmentResult:
        self.logger.log(f"Running cleansing and enrichment for {command.record_id}")
        generated_label = await self.llm_service.generate_text(f"Enrich record {command.record_id}")
        proposed_values = dict(command.values)
        proposed_values.setdefault("enrichment_note", generated_label)
        entity = CleansingResult(id=str(uuid4()), record_id=command.record_id, proposed_values=proposed_values, provenance={"source": "stub"}, confidence=0.68)
        saved = await self.result_repository.save_result(entity)
        return CleansingEnrichmentResult(result_id=saved.id or "", record_id=saved.record_id, proposed_values=saved.proposed_values, confidence=saved.confidence)
