"""AI / LLM infrastructure factory.

Re-exports ``create_llm_provider()`` from the factory module.
Provider implementations are imported lazily to avoid hard dependency
on the ``openai`` package at import time.
"""

from app.layer4_frameworks.ai.llm_factory import create_llm_provider

__all__ = [
    "create_llm_provider",
]
