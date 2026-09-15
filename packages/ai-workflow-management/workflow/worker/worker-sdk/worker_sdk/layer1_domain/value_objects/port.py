from dataclasses import dataclass
from typing import Any


@dataclass(kw_only=True)
class Port:
    id: str = ""
    label: str = ""
    required: bool = True
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "required": self.required,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Port":
        return cls(
            id=data.get("id", ""),
            label=data.get("label", ""),
            required=data.get("required", True),
            description=data.get("description", ""),
        )


def default_task_ports() -> dict[str, list[dict[str, Any]]]:
    """Return the default TASK node ports (in_default + success/failure).

    Matches the built-in TASK descriptor in the control plane's node_type_registry.
    Returns a fresh copy each time so callers can mutate safely.
    """
    return {
        "in": [Port(id="in_default", label="Input").to_dict()],
        "out": [
            Port(id="success", label="Success").to_dict(),
            Port(id="failure", label="Failure").to_dict(),
        ],
    }
