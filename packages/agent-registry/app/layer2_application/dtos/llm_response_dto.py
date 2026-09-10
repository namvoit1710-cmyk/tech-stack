"""LLM Response DTO - Data transfer object for LLM responses in the application."""
from dataclasses import dataclass

@dataclass
class LLMResponseDTO:
    """Data transfer object for LLM responses."""
    content: str
    metadata: dict | None = None