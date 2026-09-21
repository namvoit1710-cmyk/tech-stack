"""Task type for the orchestrator async chat API (SA-2114, SA-2168).

`AGENT`           -> route the task directly to the business agent named by
                     ``parameters.agent_id`` (no LLM classification, no fallback).
`RENAME`          -> set the conversation title from ``message`` (deterministic,
                     no LLM, no Business Agent).
absent/empty type -> existing LLM-routed behavior (default routing, no literal needed).
"""

from enum import Enum


class TaskType(str, Enum):
    AGENT = "AGENT"
    RENAME = "RENAME"

    @classmethod
    def from_value(cls, value: object) -> "TaskType | None":
        """Resolve an incoming ``type`` value.

        An absent/empty value means default (LLM) routing -> ``None``. A
        non-empty value that is not a known member raises ``ValueError`` so
        the caller can reject it as an invalid request.
        """
        if value is None or value == "":
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            for member in cls:
                if member.value == value:
                    return member
        raise ValueError(f"Unsupported task type: {value!r}")
