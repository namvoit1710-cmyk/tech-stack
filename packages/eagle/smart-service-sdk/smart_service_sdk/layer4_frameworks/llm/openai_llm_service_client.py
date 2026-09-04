from __future__ import annotations

import json

from smart_service_sdk.layer2_application.interfaces.generation_client_interface import (
    GenerationMessage,
    GenerationRequest,
    IGenerationClient,
)
from smart_service_sdk.layer2_application.interfaces.llm_service_interface import ILlmServiceClient


class OpenAILlmServiceClient(ILlmServiceClient):
    def __init__(self, generation_client: IGenerationClient):
        self._generation_client = generation_client

    async def expand_terms(self, text: str) -> list[str]:
        prompt = (
            "Return a compact JSON array of useful duplicate-search acronyms, "
            "synonyms, and alternate spellings for this value. Include the original "
            f"value. Value: {text}"
        )
        response = await self._generation_client.generate(
            GenerationRequest(
                messages=(GenerationMessage(role="user", content=prompt),),
                temperature=0.0,
                max_output_tokens=200,
                response_format="json_object",
            )
        )
        return self._parse_terms(response.output_text, text)

    async def generate_text(self, prompt: str) -> str:
        response = await self._generation_client.generate(
            GenerationRequest(
                messages=(GenerationMessage(role="user", content=prompt),),
                temperature=0.0,
            )
        )
        return response.output_text

    @staticmethod
    def _parse_terms(output_text: str, original: str) -> list[str]:
        terms = [original]
        try:
            payload = json.loads(output_text)
        except json.JSONDecodeError:
            payload = output_text

        raw_terms: object
        if isinstance(payload, dict):
            raw_terms = payload.get("terms") or payload.get("values") or payload
        else:
            raw_terms = payload

        if isinstance(raw_terms, dict):
            raw_terms = list(raw_terms.values())
        if isinstance(raw_terms, str):
            raw_terms = [raw_terms]

        if isinstance(raw_terms, list):
            for item in raw_terms:
                if isinstance(item, list):
                    for nested_item in item:
                        OpenAILlmServiceClient._append_term(terms, nested_item)
                    continue
                OpenAILlmServiceClient._append_term(terms, item)
        return terms

    @staticmethod
    def _append_term(terms: list[str], value: object) -> None:
        term = str(value).strip()
        if term and term not in terms:
            terms.append(term)
