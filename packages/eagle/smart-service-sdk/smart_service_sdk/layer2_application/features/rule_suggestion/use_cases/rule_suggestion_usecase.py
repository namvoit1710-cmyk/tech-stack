from dataclasses import dataclass
from uuid import uuid4

from smart_service_sdk.layer1_domain.entities.rule_suggestion import RuleSuggestion
from smart_service_sdk.layer2_application.interfaces.llm_service_interface import ILlmServiceClient
from smart_service_sdk.layer2_application.interfaces.logger_interface import ILogger
from smart_service_sdk.layer2_application.repositories.result_repository_interface import IResultRepository


@dataclass
class RuleSuggestionCommand:
    request_id: str
    change_summary: str


@dataclass
class RuleSuggestionResult:
    result_id: str
    request_id: str
    suggestions: list[str]
    rationale: str


class RuleSuggestionUseCase:
    def __init__(self, logger: ILogger, llm_service: ILlmServiceClient, result_repository: IResultRepository):
        self.logger = logger
        self.llm_service = llm_service
        self.result_repository = result_repository

    async def execute(self, command: RuleSuggestionCommand) -> RuleSuggestionResult:
        self.logger.log(f"Generating rule suggestion for {command.request_id}")
        rationale = await self.llm_service.generate_text(f"Suggest rules for: {command.change_summary}")
        entity = RuleSuggestion(id=str(uuid4()), request_id=command.request_id, suggestions=["Validate mandatory fields", "Block duplicate identifiers"], rationale=rationale)
        saved = await self.result_repository.save_result(entity)
        return RuleSuggestionResult(result_id=saved.id or "", request_id=saved.request_id, suggestions=saved.suggestions, rationale=saved.rationale)
