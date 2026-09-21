"""Domain entity: a validated incoming request.

Rich entity (AGENTS.md §3) — ``from_state`` enforces the required-input rule and
narrows the raw transport payload; ``is_generate_polar`` encapsulates the flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import cast

GENERATE_POLAR = "generate_polar"


@dataclass
class ValidationRequest:
    message: str
    params: dict[str, object]

    @classmethod
    def from_state(cls, state: dict[str, object]) -> "ValidationRequest":
        """Validate + build from graph/transport state.

        Valid when it carries ``parameters.rules`` OR a top-level ``message``.
        """
        raw_params = state.get("parameters")
        params: dict[str, object] = cast("dict[str, object]", raw_params) if isinstance(raw_params, dict) else {}
        message_value = state.get("message")
        message = message_value if isinstance(message_value, str) else ""

        if params.get("rules") is None and not message:
            raise ValueError("Missing required parameters: ['message']")
        return cls(message=message, params=params)

    def is_generate_polar(self) -> bool:
        return self.params.get("polar_type", "") == GENERATE_POLAR
