from typing import Protocol, runtime_checkable


@runtime_checkable
class ILLMService(Protocol):
    """The LLM chat-completion seam.

    The shared ``agent_sdk.OpenAIService`` satisfies this structurally, so the
    planner never imports a provider directly (keeps the single LLM egress seam).
    """

    async def get_chat_completion(self, system_prompt: str, user_prompt: str) -> str: ...
