from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.gateway.function_param import VALID_PARAM_TYPES


@dataclass(frozen=True)
class OutputField:
    """One declared output field of a gateway function (drives the generated
    NodeDefinition's output_mapping in P3). Self-validating."""

    key: str
    type: str = "string"
    label: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValidationError("output field 'key' must not be empty")
        if self.type not in VALID_PARAM_TYPES:
            raise ValidationError(
                f"output field '{self.key}' has invalid type '{self.type}'; "
                f"allowed: {sorted(VALID_PARAM_TYPES)}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "type": self.type, "label": self.label}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutputField":
        return cls(
            key=data.get("key", ""),
            type=data.get("type", "string"),
            label=data.get("label", ""),
        )
