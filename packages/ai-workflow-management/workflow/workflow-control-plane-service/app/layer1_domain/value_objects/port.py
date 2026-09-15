from dataclasses import dataclass, field
from typing import Any


def has_io_port(ports: Any) -> bool:
    """Domain rule: a node must declare at least one input *or* output port.

    Operates on the serialized ``{"in": [...], "out": [...]}`` port shape used
    across the catalog and workflow surfaces. A node with neither is malformed —
    it has no handle to wire into a graph.
    """
    if not isinstance(ports, dict):
        return False
    return bool(ports.get("in") or ports.get("out"))


@dataclass(kw_only=True)
class Port:
    id: str = ""
    label: str = ""
    data_type: str = "any"
    required: bool = True
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "data_type": self.data_type,
            "required": self.required,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Port":
        return cls(
            id=data.get("id", ""),
            label=data.get("label", ""),
            data_type=data.get("data_type", "any"),
            required=data.get("required", True),
            description=data.get("description", ""),
        )
