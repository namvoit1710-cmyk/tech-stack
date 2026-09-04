from __future__ import annotations

import json
from collections.abc import AsyncIterator

from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationRequest,
    GenerationResponse,
    GenerationStreamEvent,
    IGenerationClient,
)


class StubGenerationClient(IGenerationClient):
    _HAZARD_KEYWORDS = {
        "solvent": "solvent",
        "paint": "flammables",
        "fuel": "fuels",
        "diesel": "fuels",
        "gasoline": "fuels",
        "lubricant": "lubricants",
        "cleaner": "cleaning_agents",
        "cleaning": "cleaning_agents",
        "acid": "corrosives",
        "corrosive": "corrosives",
        "battery": "batteries",
        "aerosol": "aerosols",
        "chemical": "chemicals",
        "pesticide": "pesticides",
        "compressed gas": "compressed_gases",
    }

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        combined_prompt = " ".join(message.content for message in request.messages)
        if request.response_format == "json_object":
            payload = self._build_json_payload(
                combined_prompt,
                web_search_requested=bool(request.tools),
            )
            return GenerationResponse(output_text=json.dumps(payload))
        return GenerationResponse(output_text=f"Stubbed response for: {combined_prompt}")

    async def stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[GenerationStreamEvent]:
        response = await self.generate(request)
        yield GenerationStreamEvent(event="token", data=response.output_text)
        yield GenerationStreamEvent(event="done", data="")

    @classmethod
    def _build_json_payload(
        cls,
        prompt: str,
        *,
        web_search_requested: bool,
    ) -> dict[str, object]:
        normalized = " ".join(prompt.lower().split())
        hazardous_categories = [
            category
            for keyword, category in cls._HAZARD_KEYWORDS.items()
            if keyword in normalized
        ]
        hazardous_categories = list(dict.fromkeys(hazardous_categories))

        if hazardous_categories:
            return {
                "decision": "required",
                "hazardous_categories": hazardous_categories,
                "confidence": 0.93 if web_search_requested else 0.88,
                "reasons": [
                    "Hazard-related keywords were detected in the submitted material details."
                ],
                "evidence": [
                    "Matched hazardous SDS indicators in the supplied material text."
                ],
            }

        return {
            "decision": "not_required",
            "hazardous_categories": [],
            "confidence": 0.72 if web_search_requested else 0.68,
            "reasons": [
                "No hazardous SDS indicators were detected in the submitted material details."
            ],
            "evidence": [
                "The supplied material text did not match the stub hazardous keyword set."
            ],
        }
