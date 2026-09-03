from typing import Protocol, runtime_checkable

from agent_sdk.layer2_application.interfaces.llm_service import ILLMService


@runtime_checkable
class IChatCompletionService(ILLMService, Protocol):
    async def get_chat_completion(
        self, system_prompt: str, user_prompt: str, json_mode: bool = True
    ) -> str: ...
