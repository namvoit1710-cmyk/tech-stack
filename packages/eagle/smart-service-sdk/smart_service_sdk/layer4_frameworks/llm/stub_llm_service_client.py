from smart_service_sdk.layer2_application.interfaces.llm_service_interface import ILlmServiceClient


class StubLlmServiceClient(ILlmServiceClient):
    async def expand_terms(self, text: str) -> list[str]:
        return [text, text.upper(), text.lower()]

    async def generate_text(self, prompt: str) -> str:
        return f"Stubbed LLM response for: {prompt}"
