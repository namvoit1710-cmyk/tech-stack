from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.layer1_domain.exceptions import InvalidInputError


@dataclass
class WorkflowDefinition:
    """Domain entity representing a workflow definition."""

    workflow_id: str
    workflow_type: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def is_default(self) -> bool:
        return self.workflow_id == "default"

    def validate(self) -> None:
        if not isinstance(self.workflow_id, str) or not self.workflow_id.strip():
            raise InvalidInputError(
                "workflow_id", "Must be a non-empty string", self.workflow_id
            )
        if self.metadata is not None and not isinstance(self.metadata, dict):
            raise InvalidInputError(
                "workflow.metadata", "Must be a dictionary when provided", self.metadata
            )

    @classmethod
    def from_mapping(cls, data: Optional[Dict[str, Any]]) -> "WorkflowDefinition":
        """Create from dictionary/mapping."""
        if not data:
            return cls(workflow_id="default")

        metadata = data.get("metadata")
        return cls(
            workflow_id=str(data.get("workflow_id", "default") or "default"),
            workflow_type=data.get("workflow_type"),
            description=data.get("description"),
            metadata=metadata if isinstance(metadata, dict) or metadata is None else {},
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "workflow_id": self.workflow_id,
            "workflow_type": self.workflow_type,
            "description": self.description,
            "metadata": self.metadata,
        }
