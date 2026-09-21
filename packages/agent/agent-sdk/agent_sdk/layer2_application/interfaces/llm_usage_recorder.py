from __future__ import annotations

from typing import Protocol

from agent_sdk.layer1_domain.entities.llm_usage import LLMUsageRecord


class ILLMUsageRecorder(Protocol):
    def record(self, record: LLMUsageRecord) -> None: ...
