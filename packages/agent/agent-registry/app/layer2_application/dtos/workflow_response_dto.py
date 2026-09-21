"""Workflow Response DTO - Output contract for workflow data."""

from dataclasses import dataclass
from datetime import datetime

from app.layer1_domain.entities.workflow import Workflow


@dataclass(frozen=True)
class WorkflowResponseDTO:
    """Output DTO for workflow data.
    
    Converts domain Workflow entity to a data transfer object
    suitable for API responses.
    """

    id: str
    name: str
    description: str
    version: str
    status: str
    input_schema: list
    output_schema: list
    main_flow: bool
    metadata: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, workflow: Workflow) -> "WorkflowResponseDTO":
        """Create DTO from Workflow entity.
        
        Args:
            workflow: Workflow domain entity
            
        Returns:
            WorkflowResponseDTO instance
        """
        return cls(
            id=workflow.id,
            name=workflow.name,
            description=workflow.description,
            version=workflow.version,
            status=workflow.status,
            input_schema=workflow.input_schema,
            output_schema=workflow.output_schema,
            main_flow=workflow.main_flow,
            metadata=workflow.metadata,
            created_at=workflow.created_at,
            updated_at=workflow.updated_at,
        )

    @classmethod
    def from_entities(cls, workflows: list[Workflow]) -> list["WorkflowResponseDTO"]:
        """Create list of DTOs from list of Workflow entities.
        
        Args:
            workflows: List of Workflow domain entities
            
        Returns:
            List of WorkflowResponseDTO instances
        """
        return [cls.from_entity(workflow) for workflow in workflows]
