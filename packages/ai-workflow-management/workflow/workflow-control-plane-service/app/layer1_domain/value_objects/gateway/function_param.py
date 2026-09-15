from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.layer1_domain.exceptions import ValidationError
from app.layer1_domain.value_objects.gateway.param_location import ParamLocation

# Param value types the registry understands (mirrors the AutoForm field types
# the FE renders and the NodeDefinition input_schema accepts in P3).
VALID_PARAM_TYPES: frozenset[str] = frozenset(
    {"string", "number", "boolean", "object", "array"}
)


@dataclass(frozen=True)
class FunctionParam:
    """One input parameter of a gateway function.

    Self-validating value object: an invalid param can never exist. ``location``
    decides where the value is placed in the upstream request (path/query/header/
    body). Serializes to/from the AutoForm-style row so it doubles as the
    generated NodeDefinition's ``input_schema`` entry in P3.
    """

    key: str
    type: str = "string"
    location: ParamLocation = ParamLocation.QUERY
    required: bool = False
    label: str = ""

    def __post_init__(self) -> None:
        if not self.key or not self.key.strip():
            raise ValidationError("function param 'key' must not be empty")
        if self.key != self.key.strip():
            raise ValidationError(f"function param key '{self.key}' must not have surrounding whitespace")
        if self.type not in VALID_PARAM_TYPES:
            raise ValidationError(
                f"function param '{self.key}' has invalid type '{self.type}'; "
                f"allowed: {sorted(VALID_PARAM_TYPES)}"
            )
        if not isinstance(self.location, ParamLocation):
            # Coerce a raw string (e.g. from_dict / DB) and fail loudly if unknown.
            object.__setattr__(self, "location", ParamLocation(str(self.location)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "type": self.type,
            "location": self.location.value,
            "required": self.required,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FunctionParam":
        return cls(
            key=data.get("key", ""),
            type=data.get("type", "string"),
            location=ParamLocation(data.get("location", "query")),
            required=bool(data.get("required", False)),
            label=data.get("label", ""),
        )
