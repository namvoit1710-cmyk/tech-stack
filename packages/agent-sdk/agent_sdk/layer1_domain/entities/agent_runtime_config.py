from dataclasses import dataclass


@dataclass
class AgentRuntimeConfig:
    system_prompt: str = ""
    max_concurrency: int = 1
    llm_model: str | None = None
    llm_provider: str | None = None
    llm_temperature: float | None = None
    llm_timeout: float | None = None
    llm_max_tokens: int | None = None
    llm_max_retries: int | None = None
